#if defined(WOT_XMPP_HOST_BUILD)
#include <cstdio>
#define ERROR_MSG(...) std::fprintf(stderr, __VA_ARGS__)
#else
#include "pch.hpp"
#include "cstdmf/debug.hpp"
#endif
// The engine PCH can already include CPython 2.7 before these STL headers.
#if defined(snprintf)
#undef snprintf
#endif
#if defined(vsnprintf)
#undef vsnprintf
#endif
#if defined(hypot)
#undef hypot
#endif
#include "stock_xmpp.hpp"
#if defined(_WIN32)
#include <winsock2.h>
#else
#include <sys/socket.h>
#endif
#include <atomic>
#include <cmath>
#include <deque>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#include <math.h>
#include <Python.h>
// CPython 2.7's private CRT aliases must not rewrite modern STL declarations.
#if defined(snprintf)
#undef snprintf
#endif
#if defined(vsnprintf)
#undef vsnprintf
#endif
#if defined(hypot)
#undef hypot
#endif
#include <client.h>
#include <connectionbosh.h>
#include <connectionhttpproxy.h>
#include <connectionlistener.h>
#include <connectiontcpclient.h>
#include <iq.h>
#include <iqhandler.h>
#include <loghandler.h>
#include <logsink.h>
#include <message.h>
#include <messagehandler.h>
#include <parser.h>
#include <presence.h>
#include <presencehandler.h>
#include <rosteritem.h>
#include <rosterlistener.h>
#include <rostermanager.h>
#include <stanzaextension.h>
#include <subscription.h>
#include <tag.h>
#include <taghandler.h>

namespace BW { namespace StockXmppDetail {
using TagPtr = std::shared_ptr<gloox::Tag>;
struct ParsedTag : gloox::TagHandler {
    TagPtr result;
    bool multiple = false;
    void handleTag(gloox::Tag* value) override {
        if (result) multiple = true;
        else result.reset(value->clone());
    }
};
TagPtr parseTag(const std::string& xml) {
    ParsedTag handler;
    gloox::Parser parser(&handler);
    std::string input = xml;
    if (parser.feed(input) != -1 || handler.multiple || !handler.result)
        throw std::runtime_error("Invalid XMPP XML element");
    return handler.result;
}
struct Value {
    enum Kind { Text, Integer, List, Tuple, Dictionary, Tag } kind = Text;
    std::string text;
    long number = 0;
    std::vector<Value> children;
    std::map<std::string, Value> fields;
    TagPtr tag;
    Value() = default;
    explicit Value(const std::string& value) : text(value) {}
    explicit Value(long value) : kind(Integer), number(value) {}
    static Value list(std::vector<Value> values = {}) { Value v; v.kind=List; v.children=std::move(values); return v; }
    static Value tuple(std::vector<Value> values = {}) { Value v=list(std::move(values));v.kind=Tuple;return v; }
    static Value dict() { Value v;v.kind=Dictionary;return v; }
    static Value element(const gloox::Tag* value) { Value v;v.kind=Tag;if(value)v.tag.reset(value->clone());return v; }
};
const char* const wgNamespace="http://wargaming.net/xmpp#v2";
const char* const clientNamespace="http://wargaming.net/xmpp#client";
const gloox::Tag* child(const gloox::Tag* parent,const std::string& name,const std::string& xmlns) {
    if(parent)for(const auto* tag:parent->children())if(tag->name()==name&&tag->xmlns()==xmlns)return tag;
    return NULL;
}
Value clanInfo(const gloox::Tag* wg) {
    if(!wg)return Value::tuple();
    const auto* id=wg->findChild("clanid");const auto* tag=wg->findChild("clantag");
    return !id||id->cdata().empty()?Value::tuple():Value::tuple({Value(id->cdata()),Value(tag?tag->cdata():std::string())});
}
Value wgInfo(const gloox::Tag* parent) {
    Value value=Value::dict();const auto* wg=child(parent,"wgexts",wgNamespace);
    if(wg){const auto* user=wg->findChild("userid");const auto* nickname=wg->findChild("nickname");
        value.fields["userId"]=Value(user?user->cdata():std::string());
        value.fields["nickname"]=Value(nickname?nickname->cdata():std::string());value.fields["clanInfo"]=clanInfo(wg);
        if(const auto* info=wg->findChild("ban-info")){
            std::vector<Value> bans;for(const auto* ban:info->findChildren("ban")){
                std::vector<Value> fields;for(const auto* key:{"source","setter","expires-at","reason","components","game"})fields.emplace_back(ban->findAttribute(key));
                bans.push_back(Value::tuple(std::move(fields)));}
            if(!bans.empty())value.fields["banInfo"]=Value::list(std::move(bans));}}
    if(const auto* client=child(parent,"wgexts-client",clientNamespace))value.fields["extsClientTag"]=Value::element(client);
    return value;
}
Value mucInfo(const gloox::Tag* parent) {
    Value value=Value::dict();const auto* muc=child(parent,"x","http://jabber.org/protocol/muc#user");if(!muc)return value;
    for(const auto* item:muc->findChildren("item"))for(const auto* key:{"affiliation","role"}){
        const std::string field=item->findAttribute(key);value.fields.erase(key);if(!field.empty())value.fields[key]=Value(field);}
    std::vector<Value> codes;for(const auto* status:muc->findChildren("status")){
        const std::string code=status->findAttribute("code");if(!code.empty())codes.emplace_back(code);}
    if(!codes.empty())value.fields["status_codes"]=Value::tuple(std::move(codes));return value;
}
const char* callbackNames[] = {"onConnect", "onReady", "onDisconnect", "onRosterResultReceived",
    "onNewRosterItem", "onRosterItemRemove", "onSubscribe", "onLog", "onHandleIq", "onRosterQuerySend",
    "onHandleMsg", "onHandleMsgError", "onHandlePresence", "onHandlePresenceError"};
enum EventID { Connected, Ready, Disconnected, Roster, RosterItem, RosterRemoved, Subscribe,
    Log, Iq, RosterQuery, Message, MessageError, Presence, PresenceError, EventCount };
struct Event { int id; std::vector<Value> arguments; };
struct Run;
std::vector<std::shared_ptr<Run>> retired;

// The original client suppresses the automatic initial presence. Preserve
// that owner: Python decides which presence to send after onReady.
class ClientWithoutPresence : public gloox::Client {
public:
    std::function<void(const gloox::Tag*)> observeTag;
    ClientWithoutPresence(const gloox::JID& jid, int port) : gloox::Client(jid, "", port) {}
    void handleTag(gloox::Tag* tag) override {
        if(observeTag)observeTag(tag);
        gloox::Client::handleTag(tag);
    }
private:
    void rosterFilled() override {
        notifyStreamEvent(gloox::StreamEventFinished);
        notifyOnConnect();
    }
};
struct RawExtension : gloox::StanzaExtension {
    std::string filter;
    TagPtr value;
    RawExtension(int id, const std::string& expression, TagPtr tag = {})
        : gloox::StanzaExtension(id), filter(expression), value(std::move(tag)) {}
    const std::string& filterString() const override { return filter; }
    gloox::StanzaExtension* newInstance(const gloox::Tag* tag) const override {
        return new RawExtension(extensionType(), filter, TagPtr(tag->clone()));
    }
    gloox::Tag* tag() const override { return value ? value->clone() : NULL; }
    gloox::StanzaExtension* clone() const override { return new RawExtension(extensionType(),filter,value); }
};
struct Options { std::string jid, host, path, proxy; int port=-1, proxyPort=-1; bool bosh=false; };
struct Run : gloox::ConnectionListener, gloox::MessageHandler, gloox::PresenceHandler,
    gloox::IqHandler, gloox::RosterListener, gloox::LogHandler {
    Options options;
    std::thread thread;
    std::atomic<bool> stopping{false}, done{false};
    std::atomic<int> state{1};
    std::mutex mutex, socketMutex;
    std::set<int> sockets;
    std::deque<std::function<void(Run&)>> commands;
    std::deque<Event> events;
    std::string fullJid;
    std::unique_ptr<ClientWithoutPresence> client;
    std::map<int, std::string> filters;
    TagPtr incomingTag;
    std::map<std::string,Value> rosterClans;
    std::map<std::string,std::string> rosterNames;
    bool terminal=false;
    explicit Run(Options value) : options(std::move(value)) {}
    ~Run() { if(thread.joinable()) thread.join(); }
    void emit(int id, std::vector<Value> args = {}) {
        std::lock_guard<std::mutex> lock(mutex);
        events.push_back({id,std::move(args)});
    }
    void enqueue(std::function<void(Run&)> command) {
        std::lock_guard<std::mutex> lock(mutex); commands.push_back(std::move(command));
    }
    void stop() {
        stopping=true;
        std::lock_guard<std::mutex> lock(socketMutex);
        for(const int socket:sockets) {
#if defined(_WIN32)
            ::shutdown(static_cast<SOCKET>(socket), SD_BOTH);
#else
            ::shutdown(socket, SHUT_RDWR);
#endif
        }
    }
    void execute();
    void observe(const gloox::Tag* tag) {
        incomingTag.reset(tag?tag->clone():NULL);
        if(!tag||tag->name()!="iq")return;
        const auto* query=child(tag,"query","jabber:iq:roster");if(!query)return;
        for(const auto* item:query->findChildren("item")){
            const std::string jid=gloox::JID(item->findAttribute("jid")).bare();
            if(item->findAttribute("subscription")=="remove"){rosterClans.erase(jid);rosterNames.erase(jid);}
            else {const auto* wg=item->findChild("wgexts");rosterClans[jid]=clanInfo(wg);
                const auto* nickname=wg?wg->findChild("nickname"):NULL;
                rosterNames[jid]=nickname?nickname->cdata():std::string();}
        }
    }
    void onConnect() override {
        if(stopping) return;
        { std::lock_guard<std::mutex> lock(mutex);fullJid=client->jid().full(); }
        if(client->authed()) { state=4;emit(Ready); }
        else { state=2;emit(Connected); }
    }
    void onDisconnect(gloox::ConnectionError reason) override {
        if(terminal) return;
        terminal=true;state=0;
        const long category=stopping?0:(reason==gloox::ConnAuthenticationFailed?1:2);
        emit(Disconnected,{Value(category),Value(stopping?"":std::string("XMPP connection ended: ")+std::to_string(int(reason)))});
    }
    bool onTLSConnect(const gloox::CertInfo& info) override { return info.status==gloox::CertOk; }
    void onStreamEvent(gloox::StreamEvent event) override {
        if(event==gloox::StreamEventAuthentication)state=2;
        else if(event==gloox::StreamEventResourceBinding || event==gloox::StreamEventRoster ||
                event==gloox::StreamEventSessionCreation)state=3;
    }
    void handleLog(gloox::LogLevel level,gloox::LogArea,const std::string& message) override {
        // No XML log subscription: authentication credentials never enter logs.
        emit(Log,{Value(long(level)),Value(std::string("Client")),Value(message)});
    }
    void handleMessage(const gloox::Message& message,gloox::MessageSession*) override {
        TagPtr tag=incomingTag;
        if(!tag)tag.reset(message.tag());
        if(message.subtype()==gloox::Message::Error)
            emit(MessageError,{Value(message.id()),Value(long(message.subtype())),Value(message.from().full()),Value::element(tag.get())});
        else emit(Message,{Value(message.id()),Value(long(message.subtype())),Value(message.body()),Value(message.from().full()),Value::element(tag.get())});
    }
    void handlePresence(const gloox::Presence& presence) override {
        TagPtr tag=incomingTag;
        if(!tag)tag.reset(presence.tag());
        if(presence.subtype()==gloox::Presence::Error)
            emit(PresenceError,{Value(presence.from().full()),Value::element(tag.get())});
        else emit(Presence,{Value(presence.from().full()),Value(long(presence.priority())),Value(presence.status()),
            Value(presence.subtype()==gloox::Presence::Invalid?0:long(presence.subtype())+1),wgInfo(tag.get()),mucInfo(tag.get())});
    }
    bool handleIq(const gloox::IQ& iq) override { incomingIQ(iq);return true; }
    void handleIqID(const gloox::IQ& iq,int) override { incomingIQ(iq); }
    void incomingIQ(const gloox::IQ& iq) {
        TagPtr tag=incomingTag;if(!tag)tag.reset(iq.tag());emit(Iq,{Value(iq.id()),Value(long(iq.subtype())),Value::element(tag.get())});
    }
    Value rosterItem(const gloox::RosterItem& item) {
        const auto subscription=item.subscription();
        long to=0,from=0;
        switch(subscription) {
        case gloox::S10nNoneOut:to=1;break;case gloox::S10nNoneIn:from=1;break;
        case gloox::S10nNoneOutIn:to=from=1;break;case gloox::S10nTo:to=2;break;
        case gloox::S10nToIn:to=2;from=1;break;case gloox::S10nFrom:from=2;break;
        case gloox::S10nFromOut:from=2;to=1;break;case gloox::S10nBoth:to=from=2;break;
        default:break;
        }
        std::vector<Value> groups;for(const auto& group:item.groups())groups.emplace_back(group);
        const auto clan=rosterClans.find(item.jidJID().bare());
        const auto nickname=rosterNames.find(item.jidJID().bare());
        return Value::tuple({Value(item.jidJID().bare()),Value(item.name().empty()&&nickname!=rosterNames.end()?nickname->second:item.name()),Value::list(groups),Value(to),Value(from),clan==rosterClans.end()?Value::tuple():clan->second});
    }
    void handleRoster(const gloox::Roster& roster) override {
        std::vector<Value> items;for(const auto& item:roster)items.push_back(rosterItem(*item.second));
        emit(Roster,{Value::list(std::move(items))});
    }
    void itemChanged(const gloox::JID& jid) {
        auto* item=client->rosterManager()->getRosterItem(jid);if(!item)return;
        emit(RosterItem,rosterItem(*item).children);
    }
    void handleItemAdded(const gloox::JID& jid) override {itemChanged(jid);}
    void handleItemSubscribed(const gloox::JID& jid) override {itemChanged(jid);}
    void handleItemUpdated(const gloox::JID& jid) override {itemChanged(jid);}
    void handleItemUnsubscribed(const gloox::JID& jid) override {itemChanged(jid);}
    void handleItemRemoved(const gloox::JID& jid) override {emit(RosterRemoved,{Value(jid.bare())});}
    void handleRosterPresence(const gloox::RosterItem&,const std::string&,gloox::Presence::PresenceType,const std::string&) override {}
    void handleSelfPresence(const gloox::RosterItem&,const std::string&,gloox::Presence::PresenceType,const std::string&) override {}
    bool handleSubscriptionRequest(const gloox::JID& jid,const std::string& message) override {
        const auto* wg=child(incomingTag.get(),"wgexts",wgNamespace);
        const auto* nickname=wg?wg->findChild("nickname"):NULL;
        Value info=wgInfo(incomingTag.get());info.fields.erase("extsClientTag");
        emit(Subscribe,{Value(jid.full()),Value(message),Value(nickname?nickname->cdata():std::string()),std::move(info)});return false;
    }
    bool handleUnsubscriptionRequest(const gloox::JID&,const std::string&) override {return false;}
    void handleNonrosterPresence(const gloox::Presence&) override {}
    void handleRosterError(const gloox::IQ& iq) override {incomingIQ(iq);}
};
class WatchedTCP : public gloox::ConnectionTCPClient {
    Run& owner;
public:
    WatchedTCP(Run& run,const std::string& host,int port)
        : gloox::ConnectionTCPClient(run.client->logInstance(),host,port),owner(run) {}
    ~WatchedTCP() override { cleanup(); }
    gloox::ConnectionBase* newInstance() const override {
        return new WatchedTCP(owner,server(),port());
    }
    gloox::ConnectionError connect() override {
        const auto result=gloox::ConnectionTCPClient::connect();
        {std::lock_guard<std::mutex> lock(owner.socketMutex);if(socket()!=-1)owner.sockets.insert(socket());}
        if(owner.stopping)owner.stop();
        return result;
    }
    void cleanup() override {
        std::lock_guard<std::mutex> lock(owner.socketMutex);owner.sockets.erase(socket());
        gloox::ConnectionTCPClient::cleanup();
    }
};
void Run::execute() {
    try {
        client.reset(new ClientWithoutPresence(gloox::JID(options.jid),options.port));
        client->observeTag=[this](const gloox::Tag* tag){observe(tag);};
        if(!options.host.empty())client->setServer(options.host);
        client->setSASLMechanisms(gloox::SaslMechAll & ~(gloox::SaslMechAnonymous | gloox::SaslMechGssapi | gloox::SaslMechNTLM));
        client->registerConnectionListener(this);client->registerMessageHandler(this);client->registerPresenceHandler(this);
        client->rosterManager()->registerRosterListener(this,false);
        client->logInstance().registerLogHandler(gloox::LogLevelWarning,gloox::LogAreaClassClient,this);
        gloox::ConnectionBase* transport=new WatchedTCP(*this,
            options.proxy.empty()?(options.host.empty()?client->jid().server():options.host):options.proxy,
            options.proxy.empty()?options.port:options.proxyPort);
        if(!options.proxy.empty())transport=new gloox::ConnectionHTTPProxy(transport,client->logInstance(),options.host,options.port);
        if(options.bosh) {
            auto* bosh=new gloox::ConnectionBOSH(transport,client->logInstance(),options.host,client->jid().server());
            if(!options.path.empty())bosh->setPath(options.path);
            transport=bosh;
        }
        transport->registerConnectionDataHandler(client.get());
        client->setConnectionImpl(transport);
        if(!stopping && client->connect(false)) {
            while(!stopping && state!=0) {
                std::deque<std::function<void(Run&)>> work;
                {std::lock_guard<std::mutex> lock(mutex);work.swap(commands);}
                for(auto& command:work) {if(stopping)break;command(*this);}
                if(stopping)break;
                if(client->recv(20000)!=gloox::ConnNoError)break;
            }
        }
        if(!terminal)onDisconnect(gloox::ConnNotConnected);
        client->disconnect();
        client.reset();
    } catch(const std::exception& error) {
        if(!terminal) {terminal=true;state=0;emit(Disconnected,{Value(stopping?0L:2L),Value(std::string(error.what()))});}
        client.reset();
    }
    done=true;
}

struct Session {
    std::shared_ptr<Run> run;
    std::deque<Event> localEvents;
    int presence=0;
    unsigned long long sequence=0;
    void stop(bool notify) {
        if(!run)return;
        if(notify&&run->state!=0)localEvents.push_back({Disconnected,{Value(0L),Value(std::string())}});
        run->stop();retired.push_back(run);run.reset();
    }
    ~Session(){stop(false);}
};
struct PyClient {PyObject_HEAD Session* session;PyObject* handlers[EventCount];};
struct PyTag {PyObject_HEAD TagPtr* tag;};
PyTypeObject clientType={PyObject_HEAD_INIT(NULL) 0};
PyTypeObject tagType={PyObject_HEAD_INIT(NULL) 0};
std::set<PyClient*> clients;

PyObject* wrapTag(TagPtr tag) {
    if(!tag){Py_RETURN_NONE;}
    auto* object=PyObject_New(PyTag,&tagType);if(!object)return NULL;
    try {object->tag=new TagPtr(std::move(tag));}catch(...){PyObject_Del(object);PyErr_NoMemory();return NULL;}
    return reinterpret_cast<PyObject*>(object);
}
PyObject* toPython(const Value& value) {
    if(value.kind==Value::Text)return PyString_FromStringAndSize(value.text.data(),static_cast<Py_ssize_t>(value.text.size()));
    if(value.kind==Value::Integer)return PyInt_FromLong(value.number);
    if(value.kind==Value::Tag)return wrapTag(value.tag);
    if(value.kind==Value::Dictionary) {
        PyObject* output=PyDict_New();if(!output)return NULL;
        for(const auto& field:value.fields){PyObject* child=toPython(field.second);if(!child||PyDict_SetItemString(output,field.first.c_str(),child)!=0){Py_XDECREF(child);Py_DECREF(output);return NULL;}Py_DECREF(child);}return output;
    }
    PyObject* output=value.kind==Value::Tuple?PyTuple_New(static_cast<Py_ssize_t>(value.children.size())):PyList_New(static_cast<Py_ssize_t>(value.children.size()));
    if(!output)return NULL;
    for(size_t index=0;index<value.children.size();++index){PyObject* child=toPython(value.children[index]);if(!child){Py_DECREF(output);return NULL;}
        if(value.kind==Value::Tuple)PyTuple_SET_ITEM(output,static_cast<Py_ssize_t>(index),child);else PyList_SET_ITEM(output,static_cast<Py_ssize_t>(index),child);}
    return output;
}
bool text(PyObject* object,std::string& output) {
    PyObject* encoded=PyUnicode_Check(object)?PyUnicode_AsUTF8String(object):NULL;
    PyObject* input=encoded?encoded:object;char* bytes=NULL;Py_ssize_t size=0;
    if(PyString_AsStringAndSize(input,&bytes,&size)!=0){Py_XDECREF(encoded);return false;}
    output.assign(bytes,static_cast<size_t>(size));Py_XDECREF(encoded);return true;
}
PyObject* failure(const std::exception& error) {PyErr_SetString(PyExc_RuntimeError,error.what());return NULL;}
void reap(bool all) {
    for(auto it=retired.begin();it!=retired.end();) {
        if(all)(*it)->stop();
        if(all||(*it)->done){if((*it)->thread.joinable())(*it)->thread.join();it=retired.erase(it);}else ++it;
    }
}
int clientTraverse(PyClient* self,visitproc visit,void* arg){for(auto* handler:self->handlers)Py_VISIT(handler);return 0;}
int clientClear(PyClient* self){if(self->session)self->session->stop(false);for(auto*& handler:self->handlers)Py_CLEAR(handler);return 0;}
void clientDealloc(PyClient* self){PyObject_GC_UnTrack(self);clients.erase(self);clientClear(self);delete self->session;Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));}
PyObject* clientNew(PyTypeObject* type,PyObject* args,PyObject* keywords) {
    if(PyTuple_Size(args)!=0 || (keywords&&PyDict_Size(keywords)!=0)){PyErr_SetString(PyExc_TypeError,"XmppClient takes no arguments");return NULL;}
    auto* self=reinterpret_cast<PyClient*>(type->tp_alloc(type,0));if(!self)return NULL;
    self->session=NULL;for(auto*& handler:self->handlers)handler=NULL;
    try {self->session=new Session;clients.insert(self);}catch(...){Py_DECREF(self);return PyErr_NoMemory();}
    return reinterpret_cast<PyObject*>(self);
}
PyObject* connectClient(PyClient* self,PyObject* args,PyObject* keywords,bool bosh) {
    const char *jid=NULL,*host="",*path="",*proxy="";int port=-1,proxyPort=-1;
    char* tcpNames[]={const_cast<char*>("jid"),const_cast<char*>("host"),const_cast<char*>("port"),NULL};
    char* boshNames[]={const_cast<char*>("jid"),const_cast<char*>("host"),const_cast<char*>("port"),const_cast<char*>("url"),const_cast<char*>("proxyHost"),const_cast<char*>("proxyPort"),NULL};
    if(bosh) {if(!PyArg_ParseTupleAndKeywords(args,keywords,"ssis|si",boshNames,&jid,&host,&port,&path,&proxy,&proxyPort))return NULL;}
    else if(!PyArg_ParseTupleAndKeywords(args,keywords,"s|si",tcpNames,&jid,&host,&port))return NULL;
    if(port<-1||port>65535||proxyPort<-1||proxyPort>65535){PyErr_SetString(PyExc_ValueError,"XMPP port is outside its native range");return NULL;}
    try {
        Options options;options.jid=jid;options.host=host;options.port=port;options.path=path;options.proxy=proxy;options.proxyPort=proxyPort;options.bosh=bosh;
        self->session->stop(false);self->session->localEvents.clear();
        auto run=std::make_shared<Run>(std::move(options));
        run->thread=std::thread([run]{run->execute();});self->session->run=run;
        reap(false);Py_RETURN_NONE;
    }catch(const std::exception& error){return failure(error);}
}
PyObject* pyConnect(PyClient* self,PyObject* args,PyObject* kw){return connectClient(self,args,kw,false);}
PyObject* pyConnectBosh(PyClient* self,PyObject* args,PyObject* kw){return connectClient(self,args,kw,true);}
PyObject* pyDisconnect(PyClient* self,PyObject* args,PyObject* kw){static char* names[]={NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"",names))return NULL;self->session->stop(true);Py_RETURN_NONE;}
std::shared_ptr<Run> active(PyClient* self){auto run=self->session->run;if(!run||run->state==0){PyErr_SetString(PyExc_RuntimeError,"XMPP client has no active connection");return {};}return run;}
PyObject* pyLogin(PyClient* self,PyObject* args,PyObject* kw){const char* password=NULL;char* names[]={const_cast<char*>("password"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"s",names,&password))return NULL;auto run=active(self);if(!run)return NULL;
    const std::string secret=password;run->enqueue([secret](Run& r){r.client->setPassword(secret);if(!r.client->login())throw std::runtime_error("XMPP login could not start");});Py_RETURN_NONE;}
PyObject* pyAddFilter(PyClient* self,PyObject* args,PyObject* kw){const char* expression=NULL;int id=0;char* names[]={const_cast<char*>("filter"),const_cast<char*>("id"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"si",names,&expression,&id))return NULL;auto run=active(self);if(!run)return NULL;
    const std::string filter=expression;run->enqueue([filter,id](Run& r){r.filters[id]=filter;r.client->registerStanzaExtension(new RawExtension(id,filter));r.client->registerIqHandler(&r,id);});Py_RETURN_NONE;}
PyObject* pyRemoveFilter(PyClient* self,PyObject* args,PyObject* kw){int id=0;char* names[]={const_cast<char*>("id"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"i",names,&id))return NULL;auto run=active(self);if(!run)return NULL;
    run->enqueue([id](Run& r){r.client->removeIqHandler(&r,id);r.client->removeStanzaExtension(id);r.filters.erase(id);});Py_RETURN_NONE;}

std::string nextID(Session& session){return "wot-"+std::to_string(++session.sequence);}
void addXML(gloox::Stanza& stanza,const std::string& xml) {
    if(!xml.empty())stanza.addExtension(new RawExtension(gloox::ExtUser+1000,"",parseTag(xml)));
}
PyObject* pyQuery(PyClient* self,PyObject* args,PyObject* kw) {
    int type=0;PyObject* object=NULL;const char* to="";
    char* names[]={const_cast<char*>("type"),const_cast<char*>("query"),const_cast<char*>("to"),NULL};
    if(!PyArg_ParseTupleAndKeywords(args,kw,"iO|s",names,&type,&object,&to))return NULL;
    if(type<0||type>3){PyErr_SetString(PyExc_ValueError,"Invalid XMPP IQ type");return NULL;}
    std::string xml;if(!text(object,xml))return NULL;auto run=active(self);if(!run)return NULL;
    try {if(!xml.empty())parseTag(xml);const std::string target=to,id=nextID(*self->session);
        run->enqueue([type,xml,target,id](Run& r){gloox::IQ iq(static_cast<gloox::IQ::IqType>(type),gloox::JID(target),id);addXML(iq,xml);r.client->send(iq,&r,0);});
        return PyString_FromStringAndSize(id.data(),static_cast<Py_ssize_t>(id.size()));
    }catch(const std::exception& error){return failure(error);}
}
PyObject* pyMessage(PyClient* self,PyObject* args,PyObject* kw) {
    int type=0;const char* to="";PyObject *bodyObject=NULL,*xmlObject=NULL;
    char* names[]={const_cast<char*>("type"),const_cast<char*>("to"),const_cast<char*>("body"),const_cast<char*>("extensions"),NULL};
    if(!PyArg_ParseTupleAndKeywords(args,kw,"isOO",names,&type,&to,&bodyObject,&xmlObject))return NULL;
    std::string body,xml;if(!text(bodyObject,body)||!text(xmlObject,xml))return NULL;auto run=active(self);if(!run)return NULL;
    try {if(!xml.empty())parseTag(xml);const std::string target=to;
        run->enqueue([type,target,body,xml](Run& r){gloox::Message message(static_cast<gloox::Message::MessageType>(type),gloox::JID(target),body);addXML(message,xml);r.client->send(message);});Py_RETURN_NONE;
    }catch(const std::exception& error){return failure(error);}
}
gloox::Presence::PresenceType presenceType(int value){return value==0?gloox::Presence::Invalid:static_cast<gloox::Presence::PresenceType>(value-1);}
PyObject* pyPresence(PyClient* self,PyObject* args,PyObject* kw) {
    int type=0;const char* to="";PyObject *xmlObject=NULL,*statusObject=NULL;
    char* names[]={const_cast<char*>("type"),const_cast<char*>("to"),const_cast<char*>("extensions"),const_cast<char*>("status"),NULL};
    if(!PyArg_ParseTupleAndKeywords(args,kw,"isOO",names,&type,&to,&xmlObject,&statusObject))return NULL;
    if(type<1||type>6){PyErr_SetString(PyExc_ValueError,"Invalid outgoing XMPP presence");return NULL;}
    std::string xml,status;if(!text(xmlObject,xml)||!text(statusObject,status))return NULL;auto run=active(self);if(!run)return NULL;
    try {if(!xml.empty())parseTag(xml);const std::string target=to;
        run->enqueue([type,target,status,xml](Run& r){gloox::Presence presence(presenceType(type),gloox::JID(target),status);addXML(presence,xml);r.client->send(presence);});Py_RETURN_NONE;
    }catch(const std::exception& error){return failure(error);}
}
PyObject* pyMUCPresence(PyClient* self,PyObject* args,PyObject* kw) {
    int type=0;const char* to="";PyObject* object=NULL;
    char* names[]={const_cast<char*>("type"),const_cast<char*>("to"),const_cast<char*>("password"),NULL};
    if(!PyArg_ParseTupleAndKeywords(args,kw,"isO",names,&type,&to,&object))return NULL;
    if(type<1||type>6){PyErr_SetString(PyExc_ValueError,"Invalid outgoing XMPP presence");return NULL;}
    std::string password;if(!text(object,password))return NULL;auto run=active(self);if(!run)return NULL;const std::string target=to;
    run->enqueue([type,target,password](Run& r){gloox::Presence presence(presenceType(type),gloox::JID(target));TagPtr x(new gloox::Tag("x"));x->setXmlns("http://jabber.org/protocol/muc");if(!password.empty())new gloox::Tag(x.get(),"password",password);presence.addExtension(new RawExtension(gloox::ExtUser+1001,"",x));r.client->send(presence);});Py_RETURN_NONE;
}
PyObject* rosterChange(PyClient* self,PyObject* args,PyObject* kw,bool remove) {
    const char *jid=NULL,*name="";PyObject* groups=Py_None;
    char* names[]={const_cast<char*>("jid"),const_cast<char*>("name"),const_cast<char*>("groups"),NULL};
    if(remove){char* keys[]={const_cast<char*>("jid"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"s",keys,&jid))return NULL;}
    else if(!PyArg_ParseTupleAndKeywords(args,kw,"s|sO",names,&jid,&name,&groups))return NULL;
    std::vector<std::string> values;
    if(groups!=Py_None){PyObject* iterator=PyObject_GetIter(groups);if(!iterator)return NULL;PyObject* item;
        while((item=PyIter_Next(iterator))!=NULL){std::string group;bool ok=text(item,group);Py_DECREF(item);if(!ok){Py_DECREF(iterator);return NULL;}values.push_back(std::move(group));}
        Py_DECREF(iterator);if(PyErr_Occurred())return NULL;}
    auto run=active(self);if(!run)return NULL;const std::string address=jid,nickname=name;
    run->enqueue([address,nickname,values,remove](Run& r){const std::string id=r.client->getID();gloox::IQ iq(gloox::IQ::Set,gloox::JID(),id);
        TagPtr query(new gloox::Tag("query"));query->setXmlns("jabber:iq:roster");auto* item=new gloox::Tag(query.get(),"item");item->addAttribute("jid",address);
        if(remove)item->addAttribute("subscription","remove");else{item->addAttribute("name",nickname);for(const auto& group:values)new gloox::Tag(item,"group",group);}
        iq.addExtension(new RawExtension(gloox::ExtUser+1002,"",query));r.client->send(iq,&r,remove?2:1);r.emit(RosterQuery,{Value(id),Value(address),Value(remove?2L:1L)});});Py_RETURN_NONE;
}
PyObject* pyAdd(PyClient* s,PyObject* a,PyObject* k){return rosterChange(s,a,k,false);}
PyObject* pyRemove(PyClient* s,PyObject* a,PyObject* k){return rosterChange(s,a,k,true);}
PyObject* subscription(PyClient* self,PyObject* args,PyObject* kw,gloox::Subscription::S10nType type) {
    const char *jid=NULL,*message="";char* names[]={const_cast<char*>("jid"),const_cast<char*>("message"),NULL};
    if(!PyArg_ParseTupleAndKeywords(args,kw,"s|s",names,&jid,&message))return NULL;auto run=active(self);if(!run)return NULL;const std::string address=jid,status=message;
    run->enqueue([address,status,type](Run& r){r.client->send(gloox::Subscription(type,gloox::JID(address),status));});Py_RETURN_NONE;
}
PyObject* pySubscribe(PyClient* s,PyObject* a,PyObject* k){return subscription(s,a,k,gloox::Subscription::Subscribe);}
PyObject* pyUnsubscribe(PyClient* s,PyObject* a,PyObject* k){return subscription(s,a,k,gloox::Subscription::Unsubscribe);}
PyObject* pySubscribed(PyClient* s,PyObject* a,PyObject* k){return subscription(s,a,k,gloox::Subscription::Subscribed);}
PyObject* pyUnsubscribed(PyClient* s,PyObject* a,PyObject* k){return subscription(s,a,k,gloox::Subscription::Unsubscribed);}
PyObject* getState(PyClient* self,void*){return PyInt_FromLong(self->session->run?self->session->run->state.load():0);}
PyObject* getJid(PyClient* self,void*){std::string jid;auto run=self->session->run;if(run){std::lock_guard<std::mutex> lock(run->mutex);jid=run->fullJid;}return PyString_FromStringAndSize(jid.data(),static_cast<Py_ssize_t>(jid.size()));}
PyObject* getPresence(PyClient* self,void*){return PyInt_FromLong(self->session->presence);}
int setPresence(PyClient* self,PyObject* value,void*){if(!value){PyErr_SetString(PyExc_TypeError,"Cannot delete XMPP presence");return -1;}long number=PyInt_AsLong(value);if(PyErr_Occurred())return -1;if(number<0||number>6){PyErr_SetString(PyExc_ValueError,"Invalid XMPP presence");return -1;}
    self->session->presence=static_cast<int>(number);auto run=self->session->run;if(run&&run->state==4&&number!=0)run->enqueue([number](Run& r){r.client->setPresence(presenceType(static_cast<int>(number)),0);});return 0;}
PyObject* getHandler(PyClient* self,void* closure){const auto index=reinterpret_cast<size_t>(closure);PyObject* handler=self->handlers[index]?self->handlers[index]:Py_None;Py_INCREF(handler);return handler;}
int setHandler(PyClient* self,PyObject* value,void* closure){if(!value|| (value!=Py_None&&!PyCallable_Check(value))){PyErr_SetString(PyExc_TypeError,"XMPP callback must be callable or None");return -1;}const auto index=reinterpret_cast<size_t>(closure);Py_INCREF(value);Py_XDECREF(self->handlers[index]);self->handlers[index]=value;return 0;}
#define XMPP_METHOD(name, function) {name,reinterpret_cast<PyCFunction>(function),METH_VARARGS|METH_KEYWORDS,NULL}
PyMethodDef methods[]={XMPP_METHOD("connect",pyConnect),XMPP_METHOD("connectBosh",pyConnectBosh),XMPP_METHOD("disconnect",pyDisconnect),XMPP_METHOD("login",pyLogin),
    XMPP_METHOD("add",pyAdd),XMPP_METHOD("remove",pyRemove),XMPP_METHOD("subscribe",pySubscribe),XMPP_METHOD("unsubscribe",pyUnsubscribe),XMPP_METHOD("setSubscribed",pySubscribed),XMPP_METHOD("setUnsubscribed",pyUnsubscribed),
    XMPP_METHOD("addStanzaFilter",pyAddFilter),XMPP_METHOD("removeStanzaFilter",pyRemoveFilter),XMPP_METHOD("sendCustomQuery",pyQuery),XMPP_METHOD("sendCustomMessage",pyMessage),XMPP_METHOD("sendCustomPresence",pyPresence),XMPP_METHOD("sendMUCPresence",pyMUCPresence),{NULL,NULL,0,NULL}};
PyGetSetDef properties[EventCount+4];

PyObject* tagNew(PyTypeObject*,PyObject*,PyObject*){PyErr_SetString(PyExc_TypeError,"PyGlooxTag nodes are produced by XmppClient");return NULL;}
void tagDealloc(PyTag* self){delete self->tag;PyObject_Del(self);}
PyObject* tagValue(PyTag* self,PyObject* args,PyObject* kw,int kind){static char* names[]={NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"",names))return NULL;const std::string value=kind==0?(*self->tag)->xml():(kind==1?(*self->tag)->cdata():(*self->tag)->name());return PyString_FromStringAndSize(value.data(),static_cast<Py_ssize_t>(value.size()));}
PyObject* tagXml(PyTag* s,PyObject* a,PyObject* k){return tagValue(s,a,k,0);}
PyObject* tagData(PyTag* s,PyObject* a,PyObject* k){return tagValue(s,a,k,1);}
PyObject* tagName(PyTag* s,PyObject* a,PyObject* k){return tagValue(s,a,k,2);}
// The exact #1513 method converts one BW::string and returns None without
// rebinding the native tree (0x0071cf60). Preserve that observable contract.
PyObject* tagSetXml(PyTag*,PyObject* args,PyObject* kw){PyObject* object=NULL;char* names[]={const_cast<char*>("xml"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"O",names,&object))return NULL;std::string xml;if(!text(object,xml))return NULL;Py_RETURN_NONE;}

PyObject* tagAttribute(PyTag* self,PyObject* args,PyObject* kw){const char* name=NULL;char* names[]={const_cast<char*>("name"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"s",names,&name))return NULL;const std::string value=(*self->tag)->findAttribute(name);return PyString_FromStringAndSize(value.data(),static_cast<Py_ssize_t>(value.size()));}
PyObject* tagFilter(PyTag* self,PyObject* args,PyObject* kw){const char* xpath=NULL;char* names[]={const_cast<char*>("xpath"),NULL};if(!PyArg_ParseTupleAndKeywords(args,kw,"s",names,&xpath))return NULL;const auto tags=(*self->tag)->findTagList(xpath);PyObject* result=PyList_New(0);if(!result)return NULL;
    for(const auto* tag:tags){PyObject* child=wrapTag(TagPtr(tag->clone()));if(!child||PyList_Append(result,child)!=0){Py_XDECREF(child);Py_DECREF(result);return NULL;}Py_DECREF(child);}return result;}
PyMethodDef tagMethods[]={XMPP_METHOD("getXml",tagXml),XMPP_METHOD("setXml",tagSetXml),XMPP_METHOD("getCData",tagData),XMPP_METHOD("findAttribute",tagAttribute),XMPP_METHOD("filterXPath",tagFilter),XMPP_METHOD("getTagName",tagName),{NULL,NULL,0,NULL}};
#undef XMPP_METHOD
bool typesReady=false;
bool prepareTypes(){
    if(typesReady)return true;
    clientType.tp_name=const_cast<char*>("XmppClient");clientType.tp_basicsize=sizeof(PyClient);clientType.tp_dealloc=reinterpret_cast<destructor>(clientDealloc);
    clientType.tp_flags=Py_TPFLAGS_DEFAULT|Py_TPFLAGS_HAVE_GC;clientType.tp_traverse=reinterpret_cast<traverseproc>(clientTraverse);clientType.tp_clear=reinterpret_cast<inquiry>(clientClear);clientType.tp_new=clientNew;clientType.tp_methods=methods;
    properties[0]={const_cast<char*>("connectionState"),reinterpret_cast<getter>(getState),NULL,NULL,NULL};
    properties[1]={const_cast<char*>("fullJid"),reinterpret_cast<getter>(getJid),NULL,NULL,NULL};
    properties[2]={const_cast<char*>("presence"),reinterpret_cast<getter>(getPresence),reinterpret_cast<setter>(setPresence),NULL,NULL};
    for(size_t index=0;index<EventCount;++index)properties[index+3]={const_cast<char*>(callbackNames[index]),reinterpret_cast<getter>(getHandler),reinterpret_cast<setter>(setHandler),NULL,reinterpret_cast<void*>(index)};
    properties[EventCount+3]={NULL,NULL,NULL,NULL,NULL};clientType.tp_getset=properties;
    tagType.tp_name=const_cast<char*>("PyGlooxTag");tagType.tp_basicsize=sizeof(PyTag);tagType.tp_dealloc=reinterpret_cast<destructor>(tagDealloc);tagType.tp_flags=Py_TPFLAGS_DEFAULT;tagType.tp_methods=tagMethods;tagType.tp_new=tagNew;
    if(PyType_Ready(&clientType)<0||PyType_Ready(&tagType)<0)return false;
    struct Constant{const char* name;long value;};
    const Constant constants[]={{"PRESENCE_UNKNOWN",0},{"PRESENCE_AVAILABLE",1},{"PRESENCE_CHAT",2},{"PRESENCE_AWAY",3},{"PRESENCE_DND",4},{"PRESENCE_XA",5},{"PRESENCE_UNAVAILABLE",6},
        {"STATE_DISCONNECTED",0},{"STATE_CONNECTING",1},{"STATE_AUTHENTICATING",2},{"STATE_INITIALIZING",3},{"STATE_CONNECTED",4},
        {"SUBSCRIPTION_OFF",0},{"SUBSCRIPTION_PENDING",1},{"SUBSCRIPTION_ON",2},{"DISCONNECT_BY_REQUEST",0},{"DISCONNECT_AUTHENTICATION",1},{"DISCONNECT_OTHER_ERROR",2},
        {"LOG_LEVEL_DEBUG",0},{"LOG_LEVEL_WARNING",1},{"LOG_LEVEL_ERROR",2},{"IQ_TYPE_GET",0},{"IQ_TYPE_SET",1},{"IQ_TYPE_RESULT",2},{"IQ_TYPE_ERROR",3},{"IQ_TYPE_INVALID",4}};
    for(const auto& constant:constants){PyObject* value=PyInt_FromLong(constant.value);if(!value)return false;const int result=PyDict_SetItemString(clientType.tp_dict,constant.name,value);Py_DECREF(value);if(result!=0)return false;}
    PyType_Modified(&clientType);typesReady=true;return true;
}
void deliver(PyClient* client,const Event& event){PyObject* handler=client->handlers[event.id];if(!handler||handler==Py_None)return;Py_INCREF(handler);PyObject* arguments=toPython(Value::tuple(event.arguments));
    PyObject* result=arguments?PyObject_CallObject(handler,arguments):NULL;Py_XDECREF(arguments);if(!result)PyErr_WriteUnraisable(handler);else Py_DECREF(result);Py_DECREF(handler);}
} // namespace StockXmppDetail

bool initialiseStockXmpp(){using namespace StockXmppDetail;if(!prepareTypes())return false;PyObject* module=PyImport_AddModule("BigWorld");if(!module)return false;
    if(PyObject_SetAttrString(module,"XmppClient",reinterpret_cast<PyObject*>(&clientType))!=0||PyObject_SetAttrString(module,"PyGlooxTag",reinterpret_cast<PyObject*>(&tagType))!=0)return false;return true;}
void tickStockXmpp(){using namespace StockXmppDetail;std::vector<PyClient*> snapshot(clients.begin(),clients.end());for(auto* client:snapshot)Py_INCREF(client);
    for(auto* client:snapshot){auto run=client->session->run;std::deque<StockXmppDetail::Event> events;if(run){std::lock_guard<std::mutex> lock(run->mutex);events.swap(run->events);}std::deque<StockXmppDetail::Event> local;local.swap(client->session->localEvents);
        for(const auto& event:local)deliver(client,event);
        for(const auto& event:events){if(client->session->run!=run)break;deliver(client,event);}Py_DECREF(client);}reap(false);}
void finiStockXmpp(){using namespace StockXmppDetail;std::vector<PyClient*> snapshot(clients.begin(),clients.end());for(auto* client:snapshot)Py_INCREF(client);for(auto* client:snapshot){clientClear(client);Py_DECREF(client);}reap(true);}
} // namespace BW
