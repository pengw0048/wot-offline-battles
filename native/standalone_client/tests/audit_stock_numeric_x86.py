"""Compare the numeric kernels with read-only exact #1513 x86 instructions.

Run with the existing pefile/Unicorn audit environment. NLS itself remains a
Windows acceptance boundary. The CRT exception hook leaves the native default
integer result intact; it does not validate floating-point status or matherr.
"""
from pathlib import Path
import argparse,ctypes,math,os,pefile,random,struct,subprocess,tempfile
import unicorn
from unicorn.x86_const import *
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--client-exe',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[3]
with tempfile.TemporaryDirectory(prefix='numeric-x86-') as tmp:
 s=Path(tmp)/'probe.cpp';s.write_text('#include "'+str(root/'native/standalone_client/bigworld/stock_numeric_format.hpp')+'"\nextern "C" { unsigned group(const wchar_t* v){return wot::stock_numeric_format::grouping(v);} unsigned digits(double v,unsigned n){return wot::stock_numeric_format::niceDigits(v,n);} long long integer(double v){return wot::stock_numeric_format::signedInteger(v);} }')
 lib=Path(tmp)/'probe.dylib';subprocess.run([os.environ.get('CXX','clang++'),'-std=c++14','-shared','-fPIC','-ffp-contract=off',str(s),'-o',str(lib)],check=True)
 dll=ctypes.CDLL(str(lib));dll.group.argtypes=[ctypes.c_wchar_p];dll.group.restype=ctypes.c_uint;dll.digits.argtypes=[ctypes.c_double,ctypes.c_uint];dll.digits.restype=ctypes.c_uint;dll.integer.argtypes=[ctypes.c_double];dll.integer.restype=ctypes.c_longlong
 p=pefile.PE(str(args.client_exe));assert p.FILE_HEADER.Machine==0x14c;d=p.get_memory_mapped_image();base=p.OPTIONAL_HEADER.ImageBase
 u=unicorn.Uc(unicorn.UC_ARCH_X86,unicorn.UC_MODE_32);u.mem_map(base,(len(d)+4095)&~4095);u.mem_write(base,d);u.mem_map(0x6000000,0x20000)
 u.mem_write(0x6010000,b'\xdd\x05'+struct.pack('<I',0x6011000))
 def hook(u,a,size,_):
  if a==0x6f4bf9:
   sp=u.reg_read(UC_X86_REG_ESP);v,ptr=struct.unpack('<dI',u.mem_read(sp,12));fraction,integer=math.modf(v)
   u.mem_write(ptr,struct.pack('<d',integer));u.mem_write(u.reg_read(UC_X86_REG_EBP)-0x34,struct.pack('<d',fraction));u.reg_write(UC_X86_REG_EIP,0x6f4c02)
  elif a==0x13d836d:
   # CRT _except1 may set status/errno. The conversion's default result is
   # subsequently selected by the original instructions; no matherr handler.
   sp=u.reg_read(UC_X86_REG_ESP);ret=struct.unpack('<I',u.mem_read(sp,4))[0];u.reg_write(UC_X86_REG_ESP,sp+4);u.reg_write(UC_X86_REG_EIP,ret)
 u.hook_add(unicorn.UC_HOOK_CODE,hook)
 rng=random.Random(1513)
 for n in range(1000):
  value=''.join(rng.choice('0123456789;abc') for _ in range(rng.randrange(1,11)))
  u.mem_write(0x6001000,value.encode('utf-16le')+b'\0\0');u.mem_write(0x600f000,struct.pack('<II',0x601f000,0x6001000));u.reg_write(UC_X86_REG_ESP,0x600f000)
  u.emu_start(0x6f3e60,0x601f000,count=1000);actual=u.reg_read(UC_X86_REG_EAX);assert actual==dll.group(value),(value,actual,dll.group(value))
 for n in range(1000):
  value=rng.uniform(-1e6,1e6) if n%2 else rng.randrange(-100000,100000)/10.**rng.randrange(1,9);maximum=rng.randrange(10)
  u.reg_write(UC_X86_REG_EBP,0x6008000);u.reg_write(UC_X86_REG_EBX,0x6001000);u.reg_write(UC_X86_REG_ESP,0x6007000)
  u.mem_write(0x600800c,struct.pack('<d',value));u.mem_write(0x6001064,b'\1');u.mem_write(0x6001068,struct.pack('<I',maximum))
  u.emu_start(0x6f4bc6,0x6f4c3a,count=1000);actual=u.reg_read(UC_X86_REG_ESI);assert actual==dll.digits(value,maximum),(value,maximum,actual,dll.digits(value,maximum))
 cases=[0.,1.,-1.,1.9,-1.9,2.**63,-2.**63,float('inf'),-float('inf'),float('nan')]+[rng.uniform(-2.**66,2.**66) for _ in range(1000)]
 for address in (0x13d8aa0,0x13d83cf):
  for value in cases:
   u.reg_write(UC_X86_REG_ESP,0x600f000);u.mem_write(0x600f000,struct.pack('<I',0x601f000));u.reg_write(UC_X86_REG_FPCW,0x37f);u.reg_write(UC_X86_REG_FPTAG,0xffff)
   if address==0x13d8aa0:
    u.mem_write(0x6011000,struct.pack('<d',value));u.emu_start(0x6010000,0x6010006,count=1)
   else:u.reg_write(UC_X86_REG_XMM0,struct.unpack('<Q',struct.pack('<d',value))[0])
   u.emu_start(address,0x601f000,count=1000);actual=(u.reg_read(UC_X86_REG_EDX)<<32)|u.reg_read(UC_X86_REG_EAX);actual=actual if actual<2**63 else actual-2**64
   assert actual==dll.integer(value),(hex(address),value,actual,dll.integer(value))
 print('Exact #1513 x86: 1000 grouping + 1000 decimal-count + 2020 conversion cases passed')
