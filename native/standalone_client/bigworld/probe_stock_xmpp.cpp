// Embed the real Python/native owner against a generated loopback XMPP peer.
#if !defined(WOT_XMPP_HOST_BUILD)
#include "cstdmf/cstdmf_windows.hpp"
#include "cstdmf/allocator.hpp"
#include "cstdmf/log_msg.hpp"
#endif
#include "stock_xmpp.hpp"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <math.h>
#include <Python.h>

namespace {
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
void python(const char* code){if(PyRun_SimpleString(code)!=0)throw std::runtime_error("Python XMPP contract failed");}
void until(const char* expression,const char* message){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
    PyObject* scope=PyModule_GetDict(PyImport_AddModule("__main__"));
    while(std::chrono::steady_clock::now()<deadline){
        BW::tickStockXmpp();
        PyObject* result=PyRun_String(expression,Py_eval_input,scope,scope);
        if(!result){PyErr_Print();throw std::runtime_error(message);}
        const int accepted=PyObject_IsTrue(result);Py_DECREF(result);
        if(accepted>0)return;if(accepted<0){PyErr_Print();throw std::runtime_error(message);}
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    throw std::runtime_error(message);
}
}
int main(int argc,char** argv){
#if !defined(WOT_XMPP_HOST_BUILD)
    BW_SYSTEMSTAGE_MAIN();BW::LogMsg::automatedTest(true);
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
#endif
    if(argc!=2){std::cerr<<"Usage: probe_stock_xmpp <loopback-port>"<<std::endl;return 2;}
    int outcome=1;Py_NoSiteFlag=1;Py_Initialize();
    try{
        require(BW::initialiseStockXmpp(),"Cannot register real XMPP owner");
        PyObject* scope=PyModule_GetDict(PyImport_AddModule("__main__"));
        PyObject* port=PyInt_FromLong(std::strtol(argv[1],NULL,10));
        require(port&&PyDict_SetItemString(scope,"port",port)==0,"Cannot publish loopback port");Py_DECREF(port);
        python(R"PY(
import BigWorld, thread, gc
main_thread=thread.get_ident()
c=BigWorld.XmppClient()
assert c.connectionState==0 and c.fullJid=='' and c.presence==0
assert (c.PRESENCE_UNKNOWN,c.PRESENCE_AVAILABLE,c.PRESENCE_UNAVAILABLE)==(0,1,6)
assert (c.STATE_DISCONNECTED,c.STATE_CONNECTED,c.IQ_TYPE_INVALID)==(0,4,4)
callbacks=('onConnect','onReady','onDisconnect','onRosterResultReceived','onNewRosterItem','onRosterItemRemove','onSubscribe','onLog','onHandleIq','onRosterQuerySend','onHandleMsg','onHandleMsgError','onHandlePresence','onHandlePresenceError')
assert all(getattr(c,n) is None for n in callbacks)
events=[]
saved=[]
def check_thread(): assert thread.get_ident()==main_thread
def connected():
    check_thread(); assert c.connectionState!=4; events.append(('connect',))
def ready(): check_thread(); events.append(('ready',))
def disconnected(reason,description): check_thread();events.append(('disconnect',reason,description))
def roster(items):
    check_thread();assert len(items)==1
    assert items[0][:5]==('friend@test.local','Friend',['Friends'],2,2)
    assert items[0][5]==(('77','TAG') if not any(e[0]=='roster' for e in events) else ());events.append(('roster',))
def message(mid,kind,body,jid,tag):
    check_thread();assert kind==1 and jid=='friend@test.local/resource'
    assert tag.getTagName()=='message'
    if mid=='incoming-1':
        assert body=='Hello loopback'
        nodes=tag.filterXPath('/message/probe/node');assert len(nodes)==1
        child=nodes[0];assert child.findAttribute('code')=='7' and child.getCData()=='kept child'
        saved.append(child);before=tag.getXml();tag.setXml('<replacement/>');assert tag.getXml()==before
        tag.setXml(u'\u6d4b\u8bd5');assert tag.getXml()==before
        for arguments in ((),(3,),('a','b')):
            try:tag.setXml(*arguments)
            except TypeError:pass
            else:raise AssertionError('setXml accepted invalid arguments')
        assert child.getTagName()=='node' and child.getCData()=='kept child'
    events.append(('message',mid))
def presence(jid,priority,status,value,wg,muc):
    check_thread();assert (jid,priority,value)==('friend@test.local/resource',2,3)
    if status=='Extended':
        assert wg['userId']=='4294967297' and wg['nickname']=='Fixture user' and wg['clanInfo']==('77','TAG')
        assert wg['banInfo']==[('3','moderator','1700000000','fixture reason','chat','wot'),('4','','','','','')]
        client=wg['extsClientTag'];assert client.getTagName()=='wgexts-client'
        assert client.filterXPath('/wgexts-client/gameHost')[0].getCData()=='fixture';saved.append(client)
        assert muc=={'affiliation':'member','role':'participant','status_codes':('110','bad-code')}
    elif status=='Partial':
        assert wg=={'userId':'','nickname':'','clanInfo':()} and muc=={'affiliation':'owner'}
    else:
        assert status in ('Loopback away','Wrong namespace') and wg=={} and muc=={}
    events.append(('presence',status))
def subscribe(jid,message,nickname,wg):
    check_thread()
    if jid=='applicant@test.local':
        assert (message,nickname)==('request','Fixture user')
        assert wg['userId']=='4294967297' and wg['clanInfo']==('77','TAG') and 'extsClientTag' not in wg
    else:assert (jid,message,nickname,wg)==('plain@test.local','plain request','',{})
    events.append(('subscribe',jid))
def roster_item(jid,name,groups,to,from_,clan):
    check_thread();assert (jid,name,groups,to,from_,clan)==('friend@test.local','Fixture user',['Friends'],2,2,('77','TAG'))
    events.append(('roster-item',))
def roster_removed(jid):
    check_thread();assert jid=='friend@test.local';events.append(('roster-removed',))
def iq(iid,kind,tag):
    check_thread();assert kind==2
    nodes=tag.filterXPath('/iq/query/item');assert len(nodes)==1
    assert nodes[0].findAttribute('key')=='alpha' and nodes[0].getCData()=='IQ child'
    saved.append(nodes[0]);events.append(('iq',iid))
c.onConnect=connected;c.onReady=ready;c.onDisconnect=disconnected
c.onRosterResultReceived=roster;c.onHandleMsg=message;c.onHandlePresence=presence;c.onHandleIq=iq;c.onSubscribe=subscribe
c.onNewRosterItem=roster_item;c.onRosterItemRemove=roster_removed
c.connect('user@test.local/resource','127.0.0.1',port)
)PY");
        until("len([e for e in events if e[0]=='connect'])==1","Loopback pre-login connection did not complete");
        python("assert not any(e[0]=='ready' for e in events)\nassert c.connectionState!=4\nc.addStanzaFilter(\"/message/probe[@xmlns='urn:wot:probe']\",100001)\nc.addStanzaFilter(\"/iq/query[@xmlns='urn:wot:probe']\",100002)\nc.login('loopback-token')\n");
        until("all(any(e[0]==kind for e in events) for kind in ('ready','roster','roster-item','roster-removed','message')) and len([e for e in events if e[0]=='presence'])==4 and len([e for e in events if e[0]=='subscribe'])==2","Real login/roster/presence/message callbacks did not complete");
        python("qid=c.sendCustomQuery(0,\"<query xmlns='urn:wot:probe'><item>request</item></query>\",'test.local')\nc.sendCustomMessage(1,'friend@test.local','Outgoing loopback','')\n");
        until("any(e[:2]==('iq',qid) for e in events) and any(e[:2]==('message','outgoing-ack') for e in events)","Real outgoing IQ/message were not acknowledged");
        python("c.disconnect()\nc.connect('user@test.local/resource','127.0.0.1',port)\n");
        until("len([e for e in events if e[0]=='connect'])==2","Reconnect did not complete");
        python("c.login('loopback-token')\n");
        until("len([e for e in events if e[0]=='ready'])==2","Second authenticated stream did not complete");
        python("c.sendCustomPresence(1,'','', 'close-second-stream')\n");
        until("any(e[0]=='disconnect' and e[1]==2 for e in events)","Remote socket close did not terminate the connection");
        BW::finiStockXmpp();BW::finiStockXmpp();
        python("gc.collect()\nassert c.connectionState==0\nassert saved[0].getCData()=='kept child' and saved[2].getCData()=='IQ child'\nassert saved[1].getTagName()=='wgexts-client' and saved[1].filterXPath('/wgexts-client/arenaLabel')[0].getCData()=='random'\nassert all(getattr(c,n) is None for n in callbacks)\nsaved[:]=[]\nc=None\ngc.collect()\n");
        std::cout<<"Real gloox split login, callback thread/schema, messages/roster/IQ, tag lifetime and socket teardown passed"<<std::endl;
        outcome=0;
    }catch(const std::exception& error){std::cerr<<"Native XMPP probe failed: "<<error.what()<<std::endl;if(PyErr_Occurred())PyErr_Print();PyRun_SimpleString("print(\"XMPP events at failure:\", events, c.connectionState)");BW::finiStockXmpp();}
    Py_Finalize();
#if !defined(WOT_XMPP_HOST_BUILD)
    BW::LogMsg::fini();
#endif
    return outcome;
}
