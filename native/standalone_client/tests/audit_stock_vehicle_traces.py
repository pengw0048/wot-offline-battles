"""Compare the trace optimizer with exact #1513 x86 instructions.

Requires pefile, unicorn and a host C++14 compiler. This executes isolated EXE
instructions, not a Windows game process. D3DX vector normalisation is an
explicit mathematical oracle boundary. Quaternion imports are excluded from
comparison; their output does not feed the audited geometry or ring changes.
"""
import argparse
import ctypes as C
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import pefile
from unicorn import Uc,UC_ARCH_X86,UC_MODE_32,UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP,UC_X86_REG_ECX,UC_X86_REG_EAX,UC_X86_REG_EIP
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--client',type=Path,default=os.environ.get('WOT_0922_CLIENT'))
options=parser.parse_args()
if not options.client:parser.error('--client or WOT_0922_CLIENT is required')
pe=pefile.PE(str(options.client/'WorldOfTanks.exe'))
if pe.FILE_HEADER.Machine!=0x14c or pe.OPTIONAL_HEADER.ImageBase!=0x400000:
    raise ValueError('The audit requires the pinned x86 client image')
u=Uc(UC_ARCH_X86,UC_MODE_32);u.mem_map(pe.OPTIONAL_HEADER.ImageBase,(pe.OPTIONAL_HEADER.SizeOfImage+4095)&~4095);u.mem_write(pe.OPTIONAL_HEADER.ImageBase,pe.get_memory_mapped_image());u.mem_map(0,0x10000);u.mem_map(0x4000000,0x80000)
pack=lambda fmt,*v:struct.pack('<'+fmt,*v)
f32=lambda v:struct.unpack('<f',pack('f',v))[0]
read=lambda a,n:struct.unpack('<'+'f'*n,u.mem_read(a,n*4))
word=lambda a:struct.unpack('<I',u.mem_read(a,4))[0]
def ret(clean):
 esp=u.reg_read(UC_X86_REG_ESP);pc=word(esp);u.reg_write(UC_X86_REG_ESP,esp+4+clean);u.reg_write(UC_X86_REG_EIP,pc)
def hook(uc,a,n,_):
 esp=u.reg_read(UC_X86_REG_ESP)
 if a in (0x11c3bbe,0x11c3c00):
  count=3 if a==0x11c3bbe else 2;out,src=word(esp+4),word(esp+8);v=read(src,count);size=math.sqrt(sum(x*x for x in v));u.mem_write(out,pack('f'*count,*[x/size if size else 0 for x in v]));u.reg_write(UC_X86_REG_EAX,out);ret(8)
 elif a==0xa54950:
  out=word(esp+12);u.mem_write(out,pack('4f',0,0,0,1));ret(0)
 elif a==0x11c3c36:
  out=word(esp+4);u.mem_write(out,pack('4f',0,0,0,1));u.reg_write(UC_X86_REG_EAX,out);ret(16)
 elif a==0x13d809e:ret(0)
for a in (0x11c3bbe,0x11c3c00,0xa54950,0x11c3c36,0x13d809e):u.hook_add(UC_HOOK_CODE,hook,begin=a,end=a)
workspace=Path(__file__).resolve().parents[1]
working=tempfile.TemporaryDirectory(prefix='stock-trace-oracle-')
library=Path(working.name)/'oracle.dylib'
subprocess.run(['c++','-std=c++14','-ffp-contract=off','-dynamiclib',
    '-I',str(workspace/'include'),str(Path(__file__).with_name('stock_vehicle_traces_oracle.cpp')),
    '-o',str(library)],check=True)
lib=C.CDLL(str(library));lib.fixture.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p];lib.fixture.restype=C.c_uint
base=0x4000000;ctx=base;strip=base+0x100;records=base+0x200;stack=base+0x70000;stop=base+0x60000

def case(seed,mode):
 r=random.Random(seed)
 width=f32(r.uniform(.3,2));length=f32(r.uniform(.5,3));angle=r.uniform(-.05,.05) if mode<3 else r.uniform(-1.5,1.5)
 first=[0,0,0,width,length,.5,0,0,0,1,0,-1,0,0,0,1,0,1000,.5,0,0,0,0,0,1]
 direction=[f32(math.sin(angle)),0,f32(math.cos(angle))]
 sign=-1 if mode==1 else 1
 last=first[:];last[0]=f32(r.uniform(-.02,.02) if mode<3 else r.uniform(-2,2));last[2]=f32(length*sign+r.uniform(-.02,.02));last[13:16]=direction
 last[16]=[0,.499,.5,.7][mode%4]
 if mode==4:last[1]=f32(r.uniform(-.2,.2))
 rev=sum(direction[i]*(last[i]-first[i]) for i in range(3))<0
 a=pack('25fiiI',*first,0,-1,0);b=pack('25fiiI',*last,0,-1,rev)
 u.mem_write(ctx,b'\0'*256);u.mem_write(ctx+0x30,pack('4f',.5,f32(.99939084),f32(f32(.05)*f32(.05)),f32(33.333332)));u.mem_write(ctx+0x40,b'\1')
 u.mem_write(strip,pack('5I',0,2,records,1,250));u.mem_write(records,a+b);u.mem_write(stack,pack('3I',stop,strip,rev));u.reg_write(UC_X86_REG_ESP,stack);u.reg_write(UC_X86_REG_ECX,ctx);u.emu_start(0xa62310,stop,count=100000)
 count=word(strip+4);original=bytes(u.mem_read(records,112*count));out=C.create_string_buffer(224);n=lib.fixture(C.c_char_p(a),C.c_char_p(b),out)
 if count!=n:return 'count',seed,mode,count,n,first,last
 expected=bytes(out)[:112*n]
 for i in range(n):
  native=struct.unpack_from('<25fiiI',original,i*112);port=struct.unpack_from('<25fiiI',expected,i*112)
  for j,(x,y) in enumerate(zip(native,port)):
   if j in [6,7,8,9,23]:continue # Imported quaternion routines and camera-distance tick are separate boundaries.
   if abs(x-y)>2e-5*max(1,abs(x),abs(y)):return 'value',seed,mode,i,j,x,y,native,port
for mode in range(5):
 for seed in range(200):
  failure=case(seed,mode)
  if failure:raise AssertionError(failure)
print('1000 exact-x86 optimizer cases matched port geometry, lifetime, material and ring behavior; D3DX quaternion imports excluded')

working.cleanup()
