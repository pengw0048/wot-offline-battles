"""Test8: actual framing, outbox, decoding, load barrier and failure ownership.

All gameplay/numeric parameters in these fixtures are synthetic. No native
WorldOfTanks scene is run. Large strings reproduce exact wire byte sizes, not
the absent original 269655-byte JSON body from the crash report.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src/res/scripts/client'), str(ROOT/'server'), str(ROOT/'tests')]
from gui.mods.offline_lan_0922 import state_transfer as st
from gui.mods.offline_lan_0922 import lan_client as lc
from gui.mods.offline_lan_0922.authority_worker import AuthorityWorkerLANClient
import lan_battle_server as sv
from effective_params_fixture import effective_params
from test_port_0922_simulation_worker import _manifest, _human_profiles, _player_hello, _worker_hello
import test_port_0922_snapshot_budget_guard as budget_fixture
import bot_state_rows


def sized(size, kind='snapshot', **fields):
    msg = dict(type=kind, protocol=5, round_id=5, authority_epoch=2,
               state_revision=3, bot_state_revision=1, server_tick=0,
               test_padding='')
    msg.update(fields)
    msg['test_padding'] = 'x' * (size - len(st.json_payload(msg)))
    assert len(st.json_payload(msg)) == size
    return msg


def wire(message):
    return st.frame_payload(message, st.json_payload(message), True)


def frames(message):
    return [json.loads(line) for line in wire(message).splitlines()]


class BufferSocket:
    def __init__(self):
        self.payloads = []
        self.closed = False
    def sendall(self, data):
        self.payloads.append(data)
    def shutdown(self, *args):
        self.closed = True
    def close(self):
        self.closed = True


class CodecTests(unittest.TestCase):
    def test_exact_report_size_and_both_sides_of_old_limit(self):
        for size in (262143, 262144, 262145, 269655, 524288, st.MAX_STATE_BYTES):
            with self.subTest(size=size):
                msg = sized(size)
                payload = wire(msg)
                self.assertTrue(all(len(line)+1 <= 262144 for line in payload.splitlines()))
                decoder = st.StreamDecoder(); result = []
                for i in range(0, len(payload), 8192):
                    result += decoder.feed(payload[i:i+8192], 0.0)
                    if i+8192 < len(payload):
                        self.assertEqual([], result)
                decoder.finish()
                self.assertEqual([msg], result)
                if size <= 262144:
                    self.assertEqual(st.json_payload(msg), payload)
                else:
                    self.assertGreater(len(payload.splitlines()), 1)

    def test_every_state_kind_roundtrips_without_field_loss(self):
        for kind in st.STATE_TYPES:
            msg = sized(269655, kind, phase='loading', map='10_hills',
                        nested={'utf8': '苏系火炮', 'real': [None, False, 1.23456789]})
            self.assertEqual([msg], st.StreamDecoder().feed(wire(msg), 0))

    def test_unnegotiated_small_frame_unchanged_large_refused(self):
        msg=sized(262144); self.assertEqual(st.json_payload(msg),st.frame_payload(msg,st.json_payload(msg),False))
        with self.assertRaisesRegex(st.TransferError,'upgrade_required'):
            st.frame_payload(sized(262145),st.json_payload(sized(262145)),False)

    def test_above_logical_limit_refused_not_truncated(self):
        with self.assertRaisesRegex(st.TransferError,'state_size_limit'):
            wire(sized(st.MAX_STATE_BYTES+1))

    def test_unsupported_large_message_cannot_be_wrapped(self):
        for kind in ('input','events','bot_state','battle_receipt','state_fragment'):
            with self.subTest(kind=kind), self.assertRaises(st.TransferError):
                wire(sized(269655,kind))

    def test_actual_utf8_tcp_split_is_decoded_only_after_complete_line(self):
        msg={'type':'notice','text':'火炮与坦克'}
        raw=(json.dumps(msg,ensure_ascii=False)+'\n').encode('utf8')
        d=st.StreamDecoder(); out=[]
        for byte in raw: out+=d.feed(bytes([byte]),0)
        self.assertEqual([msg],out)

    def test_coalesced_messages_and_fragment_parts_keep_original_order(self):
        a=sized(269655);b=sized(450000,'battle_start',round_id=6)
        ping=dict(type='pong',seq=3)
        d=st.StreamDecoder()
        self.assertEqual([ping,a,b,ping], d.feed(st.json_payload(ping)+wire(a)+wire(b)+st.json_payload(ping),0))

    def test_missing_tail_eof_fails_without_releasing_state(self):
        d=st.StreamDecoder(); raw=wire(sized(269655));cut=raw.rindex(b'\n',0,-1)+1
        self.assertEqual([], d.feed(raw[:cut],0))
        with self.assertRaisesRegex(st.TransferError,'incomplete_eof'): d.finish()

    def test_timeout_is_bounded_even_with_duplicate_first_fragment(self):
        first=frames(sized(269655))[0];r=st.StateReceiver()
        self.assertIsNone(r.accept(first,0));self.assertIsNone(r.accept(first,14))
        with self.assertRaisesRegex(st.TransferError,'incomplete_timeout'):r.check_timeout(15)
        self.assertIsNone(r.active);self.assertEqual([],r.data)

    def test_corrupt_bytes_fail_checksum(self):
        rows=frames(sized(269655));tail=rows[-1]['data'];rows[-1]['data']=('Y' if tail[0]!='Y' else 'Z')+tail[1:]
        r=st.StateReceiver()
        for row in rows[:-1]: self.assertIsNone(r.accept(row,0))
        with self.assertRaisesRegex(st.TransferError,'state_integrity'):r.accept(rows[-1],0)

    def test_mixed_round_authority_revision_digest_all_fail(self):
        for field,value in [('round_id',6),('authority_epoch',3),('bot_state_revision',2),('state_revision',4)]:
            rows=frames(sized(269655)); rows[1]['scope'][field]=value;r=st.StateReceiver();r.accept(rows[0],0)
            with self.subTest(field=field),self.assertRaisesRegex(st.TransferError,'mixed_transfer'):r.accept(rows[1],0)
        rows=frames(sized(269655));r=st.StateReceiver();r.accept(rows[0],0);rows[1]['sha256']='0'*64
        with self.assertRaisesRegex(st.TransferError,'mixed_transfer'):r.accept(rows[1],0)

    def test_envelope_scope_must_match_original_payload(self):
        rows=frames(sized(269655))
        for row in rows:row['scope']['round_id']=7
        r=st.StateReceiver()
        for row in rows[:-1]:r.accept(row,0)
        with self.assertRaises(st.TransferError):r.accept(rows[-1],0)

    def test_order_missing_first_and_conflicting_duplicate(self):
        rows=frames(sized(450000));r=st.StateReceiver()
        with self.assertRaisesRegex(st.TransferError,'missing_first_part'):r.accept(rows[1],0)
        r=st.StateReceiver();r.accept(rows[0],0)
        with self.assertRaisesRegex(st.TransferError,'part_order'):r.accept(rows[2],0)
        r=st.StateReceiver();r.accept(rows[0],0);dup=copy.deepcopy(rows[0]);dup['data']='Y'+dup['data'][1:]
        with self.assertRaisesRegex(st.TransferError,'conflicting_duplicate'):r.accept(dup,0)

    def test_exact_duplicate_does_not_complete_twice(self):
        msg=sized(269655); rows=frames(msg);r=st.StateReceiver();out=[]
        for row in [rows[0],rows[0]]+rows[1:]:
            value=r.accept(row,0)
            if value is not None:out.append(value)
        self.assertEqual([msg],out)

    def test_huge_lengths_bad_types_and_invalid_base64_fail_closed(self):
        for key,value in [('size',10**30),('size',True),('parts',True),('parts',100000),('index',-1),('protocol',True),('protocol',1.0),('sha256','x'*64),('data','!')]:
            row=frames(sized(269655))[0];row[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(st.TransferError):st.StateReceiver().accept(row,0)

    def test_regular_message_cannot_overtake_incomplete_state(self):
        r=st.StateReceiver();r.accept(frames(sized(269655))[0],0)
        with self.assertRaisesRegex(st.TransferError,'interleaved_message'):r.accept({'type':'battle_live'},0)

    def test_physical_line_limit_not_raised(self):
        self.assertEqual(262144,sv.MAX_LINE_BYTES);self.assertEqual(262144,lc.MAX_MESSAGE_BYTES)
        with self.assertRaisesRegex(st.TransferError,'frame_size_limit'):st.StreamDecoder().feed(st.json_payload(sized(262145)),0)

    def test_new_connection_has_no_old_partial_transfer(self):
        rows=frames(sized(269655));old=st.StateReceiver();old.accept(rows[0],0)
        fresh=st.StateReceiver()
        with self.assertRaises(st.TransferError):fresh.accept(rows[1],0)
        self.assertEqual([sized(269655)],st.StreamDecoder().feed(wire(sized(269655)),0))

    def test_broken_diagnostic_callback_cannot_alter_state(self):
        def broken(*args):raise IOError('logging unavailable')
        msg=sized(269655);self.assertEqual([msg],st.StreamDecoder(broken).feed(wire(msg),0))


def make_room(humans=2,capable=True):
    state=sv.BattleState(map_name='10_hills',team_size=15)
    wh=_worker_hello()
    if capable:wh['capabilities'].append(st.CAPABILITY)
    worker,error=state.add_simulation_worker(BufferSocket(),('127.0.0.1',9000),wh)
    assert error is None,error
    players=[]
    for i in range(humans):
        hello=_player_hello('Player-%d'%i);hello['capabilities'].append(st.CAPABILITY)
        if i==0:hello['vehicle']='ussr:R27_SU-14'
        if i==1:hello['vehicle']='usa:A13_T34_hvy'
        player,error=state.add_player(BufferSocket(),('127.0.0.1',9001+i),hello)
        assert error is None,error
        players.append(player)
    start,error=state.request_start(state.host_player_id)
    assert error is None,error
    manifest=_manifest(bot_state_rows.bots(start))
    assert state.update_bot_manifest(-1,dict(round_id=state.round_id,bots=manifest,player_collision_profiles=_human_profiles(start['players'])))
    return state,players,worker,start


class TransportAndBarrierTests(unittest.TestCase):
    def setUp(self):
        self.log=mock.patch.object(sv,'_server_log');self.log.start();self.addCleanup(self.log.stop)
        self.limited=mock.patch.object(sv,'_server_log_limited');self.limited.start();self.addCleanup(self.limited.stop)

    def test_loading_same_269655_bytes_delivered_to_two_players_and_worker(self):
        state,players,worker,start=make_room();snap=state.loading_snapshot();snap['test_padding']=''
        snap['test_padding']='x'*(269655-len(st.json_payload(snap)))
        self.assertEqual(28,len(snap['bots']));self.assertEqual(2,len(snap['players']))
        self.assertTrue(state.broadcast_loading_transition(start))
        for endpoint in players+[worker]:endpoint.conn.payloads=[]
        with mock.patch.object(state,'loading_snapshot',return_value=snap):
            self.assertTrue(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
        for endpoint in players+[worker]:
            self.assertTrue(endpoint.connected)
            raw=b''.join(endpoint.conn.payloads)
            self.assertEqual([snap],st.StreamDecoder().feed(raw,0))
            self.assertTrue(all(len(x)+1<=262144 for x in raw.splitlines()))
        self.assertEqual('loading',state.phase)
        self.assertIsNone(state.mark_battle_ready(players[0].player_id,{'round_id':state.round_id}))
        self.assertIsNone(state.mark_battle_ready(players[1].player_id,{'round_id':state.round_id}))
        self.assertEqual('loading',state.phase)
        self.assertIsNotNone(state.mark_battle_ready(-1,{'round_id':state.round_id}))
        self.assertEqual('battle',state.phase)
        self.assertFalse(state.result_receipts)

    def test_room_sizes_preserve_every_participant_and_bot(self):
        for humans in (1,2,10,30):
            with self.subTest(humans=humans):
                state,players,worker,start=make_room(humans)
                snap=state.loading_snapshot();snap['test_padding']='z'*300000
                with mock.patch.object(state,'loading_snapshot',return_value=snap):
                    self.assertTrue(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
                for endpoint in (players[0],players[-1],worker):
                    value=st.StreamDecoder().feed(b''.join(endpoint.conn.payloads),0)[-1]
                    self.assertEqual(humans,len(value['players']));self.assertEqual(30-humans,len(value['bots']))
                    self.assertEqual(snap,value)

    def test_bad_local_encoding_cancels_load_without_dropping_peers(self):
        state,players,worker,start=make_room();snap=state.loading_snapshot();snap['not_serializable']=object()
        with mock.patch.object(state,'loading_snapshot',return_value=snap):
            self.assertFalse(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
        self.assertEqual('waiting',state.phase);self.assertFalse(state.result_receipts)
        for endpoint in players+[worker]:
            self.assertTrue(endpoint.connected);self.assertFalse(endpoint.conn.closed)
            received=st.StreamDecoder().feed(b''.join(endpoint.conn.payloads),0)
            self.assertFalse(any(m['type']=='snapshot' for m in received))
            self.assertEqual('roster',received[-1]['type'])

    def test_logical_overflow_is_room_failure_not_three_broken_sockets(self):
        state,players,worker,start=make_room();snap=state.loading_snapshot();snap['test_padding']='x'*st.MAX_STATE_BYTES
        with mock.patch.object(state,'loading_snapshot',return_value=snap):
            self.assertFalse(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
        self.assertEqual('waiting',state.phase)
        for endpoint in players+[worker]:self.assertTrue(endpoint.connected)
        self.assertFalse(state.result_receipts)
        errors=st.StreamDecoder().feed(b''.join(players[0].conn.payloads),0)
        self.assertEqual('loading_state_size_limit',errors[0]['code'])

    def test_old_game_client_requires_update_before_any_partial_load_is_sent(self):
        state,players,worker,start=make_room();players[1].capabilities=tuple(x for x in players[1].capabilities if x!=st.CAPABILITY)
        snap=state.loading_snapshot();snap['test_padding']='x'*300000
        with mock.patch.object(state,'loading_snapshot',return_value=snap):
            self.assertFalse(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
        self.assertEqual('waiting',state.phase)
        for endpoint in players+[worker]:
            self.assertTrue(endpoint.connected)
            rows=st.StreamDecoder().feed(b''.join(endpoint.conn.payloads),0)
            self.assertFalse(any(m['type']=='snapshot' for m in rows))
        self.assertEqual('loading_upgrade_required',json.loads(players[0].conn.payloads[0])['code'])

    def test_real_socket_outbox_fragments_do_not_interleave_with_live_barrier(self):
        a,b=socket.socketpair(); a.settimeout(.2);b.settimeout(2)
        p=sv.Player(1,a,('local',0),capabilities=(st.CAPABILITY,))
        msg=sized(900000);live=dict(type='battle_live',protocol=5,round_id=5)
        got=[];failure=[]
        def read():
            d=st.StreamDecoder()
            try:
                while len(got)<2:got.extend(d.feed(b.recv(4096),time.monotonic()))
            except Exception as e:failure.append(str(e))
        t=threading.Thread(target=read);t.start()
        try:
            self.assertTrue(p.offer_reliable(msg));self.assertTrue(p.offer_reliable(live))
            t.join(3);self.assertFalse(t.is_alive());self.assertFalse(failure)
            self.assertEqual([msg,live],got);self.assertTrue(p.connected)
        finally:
            p.disconnect();a.close();b.close();t.join(1)
            if p._outbox_thread:p._outbox_thread.join(1)

    def test_real_tcp_visible_and_authority_clients_reassemble_then_enter_battle(self):
        # Actual socket workers, queued main-thread dispatch and native protocol
        # validators are used. Only the BigWorld callback scheduler and vehicle
        # descriptors are fixtures; no map/renderer is started.
        from test_port_0922_lan_client_queue import QueueBigWorld
        state=sv.BattleState(map_name='10_hills',max_players=30,team_size=15)
        server=sv.ThreadedTCPServer(('127.0.0.1',0),sv.ClientHandler)
        server.game_server=type('GameServer',(),{'state':state})()
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.daemon=True;thread.start()
        events=[[],[],[]];clients=[];host,port=server.server_address
        worker=AuthorityWorkerLANClient(host,port,on_event=lambda k,m:events[2].append((k,m)),bigworld=QueueBigWorld())
        players=[lc.LANClient(host,port,'Player%d'%i,vehicle,max_health=90,
                 bigworld=QueueBigWorld(),vehicle_compact_descr='dGVzdA==',
                 effective_params=effective_params(),
                 on_event=lambda k,m,i=i:events[i].append((k,m)))
                 for i,vehicle in enumerate(('ussr:R27_SU-14','usa:A13_T34_hvy'))]
        clients=[worker]+players
        def until(predicate):
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                for c in clients:c._poll()
                if predicate():return
                time.sleep(.005)
            self.fail('TCP clients did not reach barrier: '+repr([(c.phase,c.last_error) for c in clients])+repr([[(k,m.get('message')) for k,m in es if k=='error'] for es in events]))
        try:
            worker.start();until(lambda:worker.ready)
            for c in players:c.start()
            until(lambda:all(c.ready for c in clients))
            self.assertTrue(all(st.CAPABILITY in c.server_capabilities for c in clients))
            start,error=state.request_start(state.host_player_id)
            self.assertIsNone(error);self.assertTrue(state.broadcast_loading_transition(start))
            until(lambda:all(c.phase=='loading' for c in clients))
            manifest=_manifest(bot_state_rows.bots(start))
            self.assertTrue(state.update_bot_manifest(-1,dict(round_id=state.round_id,bots=manifest,player_collision_profiles=_human_profiles(start['players']))))
            snap=state.loading_snapshot();snap['test_padding']=''
            snap['test_padding']='x'*(269655-len(st.json_payload(snap)))
            with mock.patch.object(state,'loading_snapshot',return_value=snap):
                self.assertTrue(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
            until(lambda:all(c.last_snapshot is not None and len(bot_state_rows.bots(c.last_snapshot))==28 for c in clients))
            self.assertTrue(all(c.connected for c in clients))
            self.assertFalse(any(k==st.FRAGMENT_TYPE or m.get('type')==st.FRAGMENT_TYPE for es in events for k,m in es))
            self.assertEqual('loading',state.phase)
            for c in players:self.assertTrue(c.send_battle_ready())
            until(lambda:all(p.battle_ready_round==state.round_id for p in state.players.values()))
            self.assertEqual('loading',state.phase)
            self.assertTrue(worker.send_battle_ready())
            until(lambda:state.phase=='battle')
            state.tick_once(1/sv.TICK_HZ)
            until(lambda:all(c.phase=='battle' for c in clients))
            self.assertFalse(state.result_receipts)
        finally:
            for c in clients:c.stop()
            for c in clients:
                if c.thread:c.thread.join(2)
            server.shutdown();server.server_close();thread.join(2)

    def test_atomic_queue_capacity_charges_whole_bundle(self):
        p=sv.Player(1,BufferSocket(),('local',0),capabilities=(st.CAPABILITY,));p._force_async_outbox=True
        p._start_outbox_locked=lambda:None
        msg=sized(269655)
        self.assertTrue(p.offer_reliable(msg))
        self.assertEqual(1,len(p._outbox_reliable))
        self.assertEqual(len(wire(msg)),p._outbox_reliable_bytes)
        self.assertEqual(wire(msg),p._outbox_reliable[0]['payload'])

    def test_true_send_failure_still_closes_only_failed_connection(self):
        state,players,worker,start=make_room()
        def fail(data):raise BrokenPipeError('real peer gone')
        players[1].conn.sendall=fail
        snap=state.loading_snapshot();snap['test_padding']='x'*300000
        with mock.patch.object(state,'loading_snapshot',return_value=snap):
            self.assertTrue(state.broadcast_loading_transition(dict(type='snapshot',round_id=state.round_id)))
        self.assertTrue(players[0].connected);self.assertTrue(worker.connected)
        self.assertFalse(players[1].connected)

    def test_large_mandatory_live_state_does_not_starve_orders_and_environment(self):
        fixture=budget_fixture.SnapshotBudgetGuardTests();state,p,c=fixture._state(base_bytes=300000)
        fixture._mark_manifest_current(state,p,sent_tick=0)
        p.capabilities=p.capabilities+(st.CAPABILITY,);p.conn=BufferSocket()
        p.bot_order_revision_sent=-1;p.destructible_revision_sent=-1
        state.tick_once(1/sv.TICK_HZ)
        values=st.StreamDecoder().feed(b''.join(p.conn.payloads),0)
        self.assertEqual(1,len(values));self.assertIn('bot_orders',values[0]);self.assertIn('destructibles',values[0])
        self.assertEqual(state.bot_orders['revision'],p.bot_order_revision_sent)
        self.assertEqual(state.destructible_revision,p.destructible_revision_sent)
        self.assertTrue(p.connected)

    def test_required_first_live_manifest_is_not_deferred_forever(self):
        fixture=budget_fixture.SnapshotBudgetGuardTests();state,p,c=fixture._state(manifest_bytes=170000,base_bytes=100000)
        p.capabilities=p.capabilities+(st.CAPABILITY,);p.conn=BufferSocket()
        state.tick_once(1/sv.TICK_HZ)
        values=st.StreamDecoder().feed(b''.join(p.conn.payloads),0)
        self.assertEqual(1,len(values));self.assertIn('bot_manifest',values[0])
        self.assertEqual(state.bot_manifest_revision,p.bot_manifest_revision_sent)
        self.assertTrue(p.connected)

    def test_legacy_launcher_probe_contract_remains_unchanged(self):
        self.assertNotIn(st.CAPABILITY,sv.MODERN_CLIENT_REQUIRED_CAPABILITIES)
        self.assertIn(st.CAPABILITY,sv.SERVER_CAPABILITIES)
        self.assertIn(st.CAPABILITY,lc.CLIENT_CAPABILITIES)
        client=AuthorityWorkerLANClient('localhost',1)
        self.assertIn(st.CAPABILITY,client._hello_payload()['capabilities'])


if __name__=='__main__':unittest.main()
