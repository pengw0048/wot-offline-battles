"""Isolated decoder for .wotlanreplay. JSON is data, never executable input.

Only the verified child generates protocol2 primitive pickles on a private
stdout pipe. This module is not imported into the game's Python interpreter.
"""
import gzip
import json
import math
import os
import pickle
import struct
import sys

MAX_LINE=2*1024*1024
MAX_TOTAL=768*1024*1024
MAX_FRAME=4*1024*1024
TYPES={'battle_live','snapshot','events','bot_observation','team_chat','team_command','team_command_terminal','player_destructible_contact_result','landing_observation_result'}


def send(kind, value):
    data=pickle.dumps((kind,value),protocol=2)
    if len(data)>MAX_FRAME:
        raise ValueError('decoded replay frame exceeds IPC bound')
    sys.stdout.buffer.write(struct.pack('!I',len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def read(path):
    count=total=0
    stamp=0.0
    with gzip.open(path,'rb') as stream:
        while True:
            raw=stream.readline(MAX_LINE+1)
            if not raw or len(raw)>MAX_LINE:
                raise ValueError('truncated or oversized replay row')
            total+=len(raw)
            if total>MAX_TOTAL:
                raise ValueError('replay exceeds logical byte limit')
            def bad(value):
                raise ValueError('non-finite JSON value')
            row=json.loads(raw.decode('utf-8'),parse_constant=bad)
            if not isinstance(row,dict):
                raise ValueError('replay row is not an object')
            if count==0:
                h=row.get('data')
                if row.get('type')!='header' or not isinstance(h,dict) or h.get('magic')!='WOT_OFFLINE_REPLAY' or h.get('schema')!=1 or h.get('client')!='0.9.22.0.1-cn-1513':
                    raise ValueError('invalid replay header')
                send('header',h)
                count=1
                continue
            t=row.get('t')
            if isinstance(t,bool) or not isinstance(t,(int,float)) or not stamp<=t<=14400 or not math.isfinite(t):
                raise ValueError('invalid replay timeline')
            stamp=t
            kind=row.get('type')
            if kind=='end':
                if row.get('records')!=count-1 or stream.read(1):
                    raise ValueError('replay count, footer or trailing data is invalid')
                send('row',row)
                return
            if kind not in ('wire','local') or not isinstance(row.get('data'),dict):
                raise ValueError('invalid replay event')
            if kind=='wire' and row['data'].get('type') not in TYPES:
                raise ValueError('non-gameplay replay message')
            send('row',row)
            count+=1


if __name__=='__main__':
    try:
        if len(sys.argv)!=2:
            raise ValueError('one replay path is required')
        read(sys.argv[1])
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as error:
        try:
            send('error',str(error)[:600])
        except Exception:
            pass
        sys.exit(1)
