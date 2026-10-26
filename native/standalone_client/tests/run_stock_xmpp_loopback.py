#!/usr/bin/env python3
"""Run a real native XMPP probe against a private TCP/SASL/XML fixture only."""
import argparse
import base64
import socket
import subprocess
import threading
import xml.etree.ElementTree as ET

WG = "<wgexts xmlns='http://wargaming.net/xmpp#v2'><userid>4294967297</userid><nickname>Fixture user</nickname><clanid>77</clanid><clantag>TAG</clantag><ban-info><ban id='ignored' source='3' setter='moderator' expires-at='1700000000' reason='fixture reason' components='chat' game='wot'/><ban source='4'/></ban-info></wgexts>"
CLIENT = "<wgexts-client xmlns='http://wargaming.net/xmpp#client'><arenaLabel>random</arenaLabel><gameHost>fixture</gameHost></wgexts-client>"
MUC = "<x xmlns='http://jabber.org/protocol/muc#user'><item affiliation='member' role='participant'/><status code='110'/><status/><status code='bad-code'/></x>"


class Peer:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = b''
        self.connection.settimeout(20)

    def send(self, xml):
        self.connection.sendall(xml.encode('utf-8'))

    def read(self):
        while True:
            self.buffer = self.buffer.lstrip()
            if self.buffer.startswith(b'<?xml'):
                end = self.buffer.find(b'?>')
                if end >= 0:
                    self.buffer = self.buffer[end + 2:]
                    continue
            if self.buffer.startswith(b'<stream:stream'):
                end = self.buffer.find(b'>')
                if end >= 0:
                    self.buffer = self.buffer[end + 1:]
                    return 'stream', None
            if self.buffer.startswith(b'</stream:stream>'):
                self.buffer = self.buffer[len(b'</stream:stream>'):]
                return 'closed', None
            if self.buffer:
                parser = ET.XMLPullParser(events=('start', 'end'))
                depth = 0
                for index, value in enumerate(self.buffer):
                    parser.feed(bytes((value,)))
                    # Expat 2.6+ may defer parsing tiny incremental chunks.
                    if hasattr(parser, 'flush'):
                        parser.flush()
                    for event, node in parser.read_events():
                        depth += 1 if event == 'start' else -1
                        if depth == 0:
                            self.buffer = self.buffer[index + 1:]
                            return node.tag.rsplit('}', 1)[-1], node
            received = self.connection.recv(8192)
            if not received:
                return 'closed', None
            self.buffer += received

    def header(self, features):
        self.send("<stream:stream xmlns='jabber:client' xmlns:stream='http://etherx.jabber.org/streams' from='test.local' id='fixture-stream' version='1.0'>" + features)

    def login(self, extensions=False):
        assert self.read()[0] == 'stream'
        self.header("<stream:features><mechanisms xmlns='urn:ietf:params:xml:ns:xmpp-sasl'><mechanism>PLAIN</mechanism></mechanisms></stream:features>")
        name, auth = self.read()
        assert name == 'auth' and auth.attrib.get('mechanism') == 'PLAIN', (name, None if auth is None else auth.tag)
        assert base64.b64decode(auth.text).split(b'\0')[-1] == b'loopback-token'
        self.send("<success xmlns='urn:ietf:params:xml:ns:xmpp-sasl'/>")
        assert self.read()[0] == 'stream'
        self.header("<stream:features><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'/><session xmlns='urn:ietf:params:xml:ns:xmpp-session'/></stream:features>")
        while True:
            name, iq = self.read()
            assert name == 'iq'
            if iq.find('{urn:ietf:params:xml:ns:xmpp-bind}bind') is not None:
                self.send("<iq type='result' id='%s'><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><jid>user@test.local/resource</jid></bind></iq>" % iq.attrib['id'])
            elif iq.find('{urn:ietf:params:xml:ns:xmpp-session}session') is not None:
                self.send("<iq type='result' id='%s'/>" % iq.attrib['id'])
            elif iq.find('{jabber:iq:roster}query') is not None:
                extension = WG if extensions else ''
                self.send("<iq type='result' id='%s'><query xmlns='jabber:iq:roster'><item jid='friend@test.local' name='Friend' subscription='both'><group>Friends</group>%s</item></query></iq>" % (iq.attrib['id'], extension))
                return
            elif iq.find('{jabber:iq:private}query') is not None:
                self.send("<iq type='result' id='%s'><query xmlns='jabber:iq:private'/></iq>" % iq.attrib['id'])
            else:
                raise AssertionError(('Unexpected login stanza', name, None if iq is None else [item.tag for item in iq]))


def exercise(listener, failures):
    try:
        connection, address = listener.accept()
        assert address[0] == '127.0.0.1'
        with connection:
            peer = Peer(connection)
            peer.login(extensions=True)
            peer.send("<message from='friend@test.local/resource' id='incoming-1' type='chat'><body>Hello loopback</body><probe xmlns='urn:wot:probe'><node code='7'>kept child</node></probe></message><presence from='friend@test.local/resource'><show>away</show><status>Loopback away</status><priority>2</priority></presence>")
            peer.send("<presence from='friend@test.local/resource'><show>away</show><status>Extended</status><priority>2</priority>%s%s%s</presence>" % (WG, CLIENT, MUC))
            peer.send("<presence from='friend@test.local/resource'><show>away</show><status>Wrong namespace</status><priority>2</priority><wgexts xmlns='urn:wrong'><userid>999</userid></wgexts><wgexts-client xmlns='urn:wrong'/><x xmlns='urn:wrong'><item role='moderator'/></x></presence>")
            peer.send("<presence type='subscribe' from='applicant@test.local'><status>request</status>%s</presence>" % WG)
            peer.send("<presence type='subscribe' from='plain@test.local'><status>plain request</status></presence>")
            peer.send("<presence from='friend@test.local/resource'><show>away</show><status>Partial</status><priority>2</priority><wgexts xmlns='http://wargaming.net/xmpp#v2'><clantag>alone</clantag><ban-info/></wgexts><x xmlns='http://jabber.org/protocol/muc#user'><item role='participant'/><item affiliation='owner'/><status code=''/></x></presence>")
            peer.send("<iq type='set' id='roster-push'><query xmlns='jabber:iq:roster'><item jid='friend@test.local' subscription='both'><group>Friends</group>%s</item></query></iq>" % WG)
            peer.send("<iq type='set' id='roster-remove'><query xmlns='jabber:iq:roster'><item jid='friend@test.local' subscription='remove'/></query></iq>")
            iq_seen = message_seen = False
            while not (iq_seen and message_seen):
                name, node = peer.read()
                if name == 'iq':
                    if node.attrib.get('id') in ('roster-push', 'roster-remove'):
                        assert node.attrib.get('type') == 'result'
                        continue
                    assert node.find('{urn:wot:probe}query') is not None
                    peer.send("<iq type='result' id='%s'><query xmlns='urn:wot:probe'><item key='alpha'>IQ child</item></query></iq>" % node.attrib['id'])
                    iq_seen = True
                elif name == 'message':
                    assert next(item.text for item in node if item.tag.rsplit('}',1)[-1]=='body') == 'Outgoing loopback'
                    message_seen = True
                else:
                    raise AssertionError('Unexpected application stanza')
            peer.send("<message from='friend@test.local/resource' id='outgoing-ack' type='chat'><body>ack</body></message>")
            assert peer.read()[0] == 'closed'
        connection, address = listener.accept()
        assert address[0] == '127.0.0.1'
        with connection:
            peer = Peer(connection)
            peer.login()
            name, presence = peer.read()
            assert name == 'presence' and next(item.text for item in presence if item.tag.rsplit('}',1)[-1]=='status') == 'close-second-stream'
            # Real remote EOF is the second session's terminal acknowledgement.
    except BaseException as error:
        failures.append(error)


def run(probe):
    failures = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        listener.settimeout(25)
        worker = threading.Thread(target=exercise, args=(listener, failures), daemon=True)
        worker.start()
        result = subprocess.run([probe, str(listener.getsockname()[1])], timeout=55)
        worker.join(timeout=26)
        if worker.is_alive():
            raise AssertionError('Loopback peer did not observe terminal socket state')
        if failures:
            raise failures[0]
        result.check_returncode()
    print('Loopback TCP/SASL/XML peer observed both completed sessions; no external endpoint was used')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_probe')
    run(parser.parse_args().native_probe)
