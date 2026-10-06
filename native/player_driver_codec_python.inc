// Private plain-data wire codec. Marshal is only a local C-speed object
// constructor/reader; incoming bytes are never passed to it unvalidated.
#include <set>
namespace driver_codec {
const size_t limit = 1024 * 1024;
const unsigned max_depth = 32;
struct Invalid {};
struct Bytes {
  std::string data;
  void add(char c) { if (data.size() >= limit + 16) throw Invalid();
    data.push_back(c); }
  void add(const std::string &s) {
    if (s.size() > limit + 16 - data.size()) throw Invalid();
    data += s;
  }
  void u32(uint32_t n) { for (int i = 0; i < 4; ++i) add(static_cast<char>(n >> (8*i))); }
  void u64(uint64_t n) { for (int i = 0; i < 8; ++i) add(static_cast<char>(n >> (8*i))); }
};
struct Reader {
  const char *p; size_t length, at;
  Reader(const char *v, size_t n): p(v), length(n), at(0) {
    if (n > limit) throw Invalid();
  }
  unsigned char byte() { if (at == length) throw Invalid(); return static_cast<unsigned char>(p[at++]); }
  uint32_t u32() { uint32_t n=0; for (int i=0;i<4;++i) n |= uint32_t(byte())<<(8*i); return n; }
  uint64_t u64() { uint64_t n=0; for (int i=0;i<8;++i) n |= uint64_t(byte())<<(8*i); return n; }
  std::string text() {
    const uint32_t n=u32(); if (n > length-at) throw Invalid();
    std::string s(p+at,n); at+=n; utf8(s); return s;
  }
  static void utf8(const std::string &s) {
    for (size_t i=0;i<s.size();) {
      const unsigned char c=static_cast<unsigned char>(s[i++]);
      if (c<128) continue;
      unsigned n; uint32_t cp, minimum;
      if (c>=0xc2 && c<=0xdf) { n=1;cp=c&31;minimum=0x80; }
      else if (c>=0xe0 && c<=0xef) { n=2;cp=c&15;minimum=0x800; }
      else if (c>=0xf0 && c<=0xf4) { n=3;cp=c&7;minimum=0x10000; }
      else throw Invalid();
      if (n>s.size()-i) throw Invalid();
      while (n--) { const unsigned char b=static_cast<unsigned char>(s[i++]);
        if ((b&0xc0)!=0x80) throw Invalid();
        cp=(cp<<6)|(b&63); }
      if (cp<minimum || cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff)) throw Invalid();
    }
  }
  void string_value(Bytes &out, const std::string &s, char tag) {
    out.add(tag);out.u32(static_cast<uint32_t>(s.size()));out.add(s);
  }
  void wire(Bytes &out, unsigned depth=0) {
    if(depth>max_depth) throw Invalid();
    const unsigned char tag=byte();
    if(tag=='n' || tag=='t' || tag=='f') { out.add(tag=='n'?'N':tag=='t'?'T':'F');return; }
    if(tag=='i') {
      const uint64_t bits=u64();int64_t n;std::memcpy(&n,&bits,8);
      if(n>=INT32_MIN && n<=INT32_MAX) {out.add('i');out.u32(static_cast<uint32_t>(n));}
      else {out.add('I');out.u64(bits);}return;
    }
    if(tag=='d') {
      const uint64_t bits=u64();double d;std::memcpy(&d,&bits,8);
      if(!std::isfinite(d)) throw Invalid();
      out.add('g');out.u64(bits);
      return;
    }
    if(tag=='s') {string_value(out,text(),'u');return;}
    if(tag=='l' || tag=='m') {
      const uint32_t n=u32();if(n>length-at) throw Invalid();
      out.add(tag=='l'?'[':'{');if(tag=='l') out.u32(n);
      std::set<std::string> keys;
      for(uint32_t i=0;i<n;++i) {
        if(tag=='m') {if(byte()!='s') throw Invalid();const std::string key=text();
          if(!keys.insert(key).second) throw Invalid();
          string_value(out,key,'u');}
        wire(out,depth+1);
      }
      if(tag=='m') out.add('0');
      return;
    }
    throw Invalid();
  }
};
PyObject *bytes(const std::string &value) {
  // Both wire magic and the internal one-item marshal tuple make length >=2.
  // PyString_FromString only caches empty/one-byte strings; this fresh owned
  // object is not exposed or hashed until its initial payload is populated.
  if(value.size()<2) throw Invalid();
  std::string allocation(value.size(),'\1');PyObject *out=string_new(allocation.c_str());
  if(!out) return 0;
#ifdef WOT_HOST_PYTHON
  char *payload=PyString_AS_STRING(out);
#else
  char *payload=reinterpret_cast<char *>(out)+20;
#endif
  std::memcpy(payload,value.data(),value.size());return out;
}
const char *payload(PyObject *value) {
  if(!value || value->ob_type!=string_type) throw Invalid();
#ifdef WOT_HOST_PYTHON
  return PyString_AS_STRING(value);
#else
  return reinterpret_cast<const char *>(value)+20;
#endif
}
struct Encoder {
  static void codepoint(std::string &out,uint32_t cp) {
    if(cp<0x80) out.push_back(static_cast<char>(cp));
    else if(cp<0x800) {
      out.push_back(static_cast<char>(0xc0|(cp>>6)));
      out.push_back(static_cast<char>(0x80|(cp&63)));
    } else if(cp<0x10000) {
      out.push_back(static_cast<char>(0xe0|(cp>>12)));
      out.push_back(static_cast<char>(0x80|((cp>>6)&63)));
      out.push_back(static_cast<char>(0x80|(cp&63)));
    } else {
      out.push_back(static_cast<char>(0xf0|(cp>>18)));
      out.push_back(static_cast<char>(0x80|((cp>>12)&63)));
      out.push_back(static_cast<char>(0x80|((cp>>6)&63)));
      out.push_back(static_cast<char>(0x80|(cp&63)));
    }
  }
  std::string text(PyObject *value) {
    if(value->ob_type==string_type) {
      const size_t n=size(value);if(n>limit) throw Invalid();
      std::string out(payload(value),n);Reader::utf8(out);return out;
    }
    if(value->ob_type!=unicode_type) throw Invalid();
#ifdef WOT_HOST_PYTHON
    const Py_ssize_t n=PyUnicode_GET_SIZE(value);
    const Py_UNICODE *p=PyUnicode_AS_UNICODE(value);
#else
    const Py_ssize_t n=read<Py_ssize_t>(reinterpret_cast<const char *>(value)+8);
    const uint16_t *p=read<const uint16_t *>(reinterpret_cast<const char *>(value)+12);
#endif
    if(n<0 || static_cast<size_t>(n)>limit) throw Invalid();
    std::string out;out.reserve(static_cast<size_t>(n));
    for(Py_ssize_t i=0;i<n;++i) {
      uint32_t cp=p[i];
      if(sizeof(*p)==2 && cp>=0xd800 && cp<=0xdbff) {
        if(++i==n || p[i]<0xdc00 || p[i]>0xdfff) throw Invalid();
        cp=0x10000+((cp-0xd800)<<10)+(p[i]-0xdc00);
      }
      if(cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff)) throw Invalid();
      codepoint(out,cp);if(out.size()>limit) throw Invalid();
    }
    return out;
  }
  int64_t integer(PyObject *value) {
    if(value->ob_type==int_type)
      return read<long>(reinterpret_cast<const char *>(value)+sizeof(PyObject));
    if(value->ob_type!=long_type) throw Invalid();
    const Py_ssize_t count=size(value);
    const uint64_t n=count<0?uint64_t(-int64_t(count)):uint64_t(count);
#ifdef WOT_HOST_PYTHON
    const digit *p=reinterpret_cast<PyLongObject *>(value)->ob_digit;
    const unsigned shift=PyLong_SHIFT;
#else
    const uint16_t *p=reinterpret_cast<const uint16_t *>(reinterpret_cast<const char *>(value)+12);
    const unsigned shift=15;
#endif
    if(n>(64+shift-1)/shift) throw Invalid();
    uint64_t magnitude=0;const uint64_t maximum=uint64_t(INT64_MAX)+(count<0?1:0);
    for(uint64_t i=0;i<n;++i) {
      const unsigned bits=static_cast<unsigned>(i*shift);
      if(uint64_t(p[i])>(maximum>>bits)) throw Invalid();
      magnitude |= uint64_t(p[i])<<bits;
    }
    if(magnitude>maximum) throw Invalid();
    const uint64_t bits=count<0?uint64_t(0)-magnitude:magnitude;
    int64_t out;std::memcpy(&out,&bits,8);return out;
  }
  void string_value(Bytes &out,const std::string &s) {
    out.add('s');out.u32(static_cast<uint32_t>(s.size()));out.add(s);
  }
  void value(Bytes &out,PyObject *v,unsigned depth=0) {
    if(!v || depth>max_depth) throw Invalid();
    if(v==none_object) {out.add('n');return;}
    if(v->ob_type==bool_type) {
      out.add(read<long>(reinterpret_cast<const char *>(v)+sizeof(PyObject))?'t':'f');return;
    }
    if(v->ob_type==int_type || v->ob_type==long_type) {
      out.add('i');out.u64(static_cast<uint64_t>(integer(v)));return;
    }
    if(v->ob_type==float_type) {
      const double d=number(v);if(!std::isfinite(d)) throw Invalid();
      uint64_t bits;std::memcpy(&bits,&d,8);out.add('d');out.u64(bits);return;
    }
    if(v->ob_type==string_type || v->ob_type==unicode_type) {string_value(out,text(v));return;}
    if(sequence(v)) {
      const size_t n=size(v);if(n>limit) throw Invalid();
      out.add('l');out.u32(static_cast<uint32_t>(n));
      for(size_t i=0;i<n;++i) value(out,items(v)[i],depth+1);
      return;
    }
    if(v->ob_type!=dict_type) throw Invalid();
#ifdef WOT_HOST_PYTHON
    auto dict=reinterpret_cast<PyDictObject *>(v);
    const Py_ssize_t mask=dict->ma_mask,used=dict->ma_used;
    auto table=dict->ma_table;
#else
    const char *dict=reinterpret_cast<const char *>(v);
    const Py_ssize_t mask=read<Py_ssize_t>(dict+16),used=read<Py_ssize_t>(dict+12);
    const char *table=read<const char *>(dict+20);
#endif
    if(used<0 || static_cast<size_t>(used)>limit) throw Invalid();
    out.add('m');out.u32(static_cast<uint32_t>(used));std::set<std::string> keys;
    for(Py_ssize_t i=0;i<=mask;++i) {
#ifdef WOT_HOST_PYTHON
      PyObject *key=table[i].me_key,*item=table[i].me_value;
#else
      PyObject *key=read<PyObject *>(table+12*i+4),*item=read<PyObject *>(table+12*i+8);
#endif
      if(!item) continue;
      std::string name=(key->ob_type==int_type || key->ob_type==long_type)
        ?std::to_string(integer(key)):text(key);
      if(!keys.insert(name).second) throw Invalid();
      string_value(out,name);value(out,item,depth+1);
    }
  }
};
}
PyObject *WOT_CDECL driver_encode(PyObject *, PyObject *args) {
  try {
    require_sequence(args,1,true);auto values=items(args);
    driver_codec::Encoder encoder;driver_codec::Bytes out;
    out.add("WDP2");encoder.value(out,values[0]);
    if(out.data.size()>driver_codec::limit) throw driver_codec::Invalid();
    return driver_codec::bytes(out.data);
  } catch(const driver_codec::Invalid &) {return fallback();}
    catch(const Unsupported &) {return fallback();}
    catch(const PythonAllocationFailed &) {return 0;}
    catch(const std::exception &) {return fallback();}
}
PyObject *WOT_CDECL driver_decode(PyObject *, PyObject *args) {
  try {
    require_sequence(args,2,true);auto values=items(args);
    const char *raw=driver_codec::payload(values[0]);const size_t length=size(values[0]);
    if(length<5 || std::memcmp(raw,"WDP2",4)) throw driver_codec::Invalid();
    driver_codec::Reader scan(raw,length);scan.at=4;driver_codec::Bytes out;
    out.add('(');out.u32(1);scan.wire(out);
    if(scan.at!=scan.length) throw driver_codec::Invalid();
    PyObject *encoded=driver_codec::bytes(out.data);if(!encoded) return 0;
    OwnedTuple call(1);call.set(0,encoded);WorldObject call_args(call.release());
    return object_call(values[1],call_args.get(),0);
  } catch(const driver_codec::Invalid &) {return fallback();}
    catch(const Unsupported &) {return fallback();}
    catch(const PythonAllocationFailed &) {return 0;}
    catch(const std::exception &) {return fallback();}
}
