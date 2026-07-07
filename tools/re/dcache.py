#!/usr/bin/env python3
# dcache.py — minimal dyld_shared_cache (v1, armv7 / iOS-5) reverse-engineering tool.
# Re-created after the Phase-1 tooling (analyze_cache.py/symgrep.py/disasm2.py) was lost.
# Handles: cache header + mappings + image list; per-image Mach-O (LC_SEGMENT/LC_SYMTAB);
# C++ symbols from the shared symtab; ObjC class/method metadata (__objc_classlist);
# Thumb-2 disassembly via capstone with PC-relative literal + selref/objc resolution.
#
# usage:
#   dcache.py CACHE images [substr]                 list images (optionally filtered)
#   dcache.py CACHE syms   IMAGE_SUBSTR [PAT]       C++ symtab symbols in image (name substring PAT)
#   dcache.py CACHE class  IMAGE_SUBSTR CLASSNAME   ObjC methods (sel -> imp addr) of a class
#   dcache.py CACHE dis    IMAGE_SUBSTR ADDR [N]    disassemble N (default 200) insns at hex ADDR
import sys, struct

class Cache:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        magic = self.d[:16].rstrip(b'\x00').decode('latin1')
        if not magic.startswith('dyld_v1'):
            raise SystemExit('not a dyld v1 cache: %r' % magic)
        self.magic = magic
        (self.mappingOffset, self.mappingCount,
         self.imagesOffset, self.imagesCount) = struct.unpack_from('<IIII', self.d, 16)
        # mappings: address(u64) size(u64) fileOffset(u64) maxProt(u32) initProt(u32)
        self.maps = []
        off = self.mappingOffset
        for _ in range(self.mappingCount):
            addr, size, foff, mx, ini = struct.unpack_from('<QQQII', self.d, off)
            self.maps.append((addr, size, foff)); off += 32
        # images: address(u64) modTime(u64) inode(u64) pathFileOffset(u32) pad(u32)
        self.images = []
        off = self.imagesOffset
        for _ in range(self.imagesCount):
            addr, mt, ino, pfo, pad = struct.unpack_from('<QQQII', self.d, off)
            path = self._cstr(pfo)
            self.images.append((addr, path)); off += 32

    def _cstr(self, off):
        e = self.d.index(b'\x00', off); return self.d[off:e].decode('latin1')

    def v2f(self, vmaddr):
        for addr, size, foff in self.maps:
            if addr <= vmaddr < addr + size:
                return foff + (vmaddr - addr)
        return None

    def find_image(self, substr):
        for addr, path in self.images:
            if substr.lower() in path.lower():
                return addr, path
        raise SystemExit('image not found: %s' % substr)

# ---- Mach-O (32-bit ARM) within the cache, header at vmaddr ----
class MachO:
    def __init__(self, cache, vmaddr):
        self.c = cache; self.base = vmaddr
        f = cache.v2f(vmaddr); d = cache.d
        magic, cputype, cpusub, ftype, ncmds, sizecmds, flags = struct.unpack_from('<IIIIIII', d, f)
        assert magic == 0xfeedface, 'not a 32-bit macho: %#x' % magic
        self.segs = {}; self.symtab = None; self.objc = {}
        off = f + 28
        for _ in range(ncmds):
            cmd, csize = struct.unpack_from('<II', d, off)
            if cmd == 0x1:  # LC_SEGMENT
                segname = d[off+8:off+24].rstrip(b'\x00').decode('latin1')
                vmaddr_, vmsize, fileoff, filesize = struct.unpack_from('<IIII', d, off+24)
                self.segs[segname] = (vmaddr_, vmsize, fileoff, filesize)
                nsects = struct.unpack_from('<I', d, off+48)[0]
                so = off + 56
                for _ in range(nsects):
                    sname = d[so:so+16].rstrip(b'\x00').decode('latin1')
                    saddr, ssize, soff = struct.unpack_from('<III', d, so+32)
                    self.objc[(segname, sname)] = (saddr, ssize, soff); so += 68
            elif cmd == 0x2:  # LC_SYMTAB
                symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', d, off+8)
                self.symtab = (symoff, nsyms, stroff, strsize)
            off += csize

    def symbols(self):
        # nlist: n_strx(u32) n_type(u8) n_sect(u8) n_desc(u16) n_value(u32) = 12 bytes
        d = self.c.d; symoff, nsyms, stroff, strsize = self.symtab
        out = []
        for i in range(nsyms):
            n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from('<IBBHI', d, symoff + i*12)
            if n_strx == 0 or n_value == 0: continue
            name = self.c._cstr(stroff + n_strx)
            out.append((name, n_value))
        return out

    def objc_classes(self):
        # __objc_classlist -> class_t {isa,super,cache,vtable,data}; data->class_ro_t
        key = ('__DATA', '__objc_classlist')
        if key not in self.objc:
            for k in self.objc:
                if k[1].startswith('__objc_classlist'): key = k; break
        if key not in self.objc: return {}
        saddr, ssize, soff = self.objc[key]; d = self.c.d
        classes = {}
        for i in range(ssize // 4):
            clsptr = struct.unpack_from('<I', d, soff + i*4)[0]
            if not clsptr: continue
            classes.update(self._read_class(clsptr))
        return classes

    def _read_class(self, clsptr):
        d = self.c.d; f = self.c.v2f(clsptr)
        if f is None: return {}
        isa, sup, cache_, vtab, data = struct.unpack_from('<IIIII', d, f)
        rof = self.c.v2f(data & ~3)
        if rof is None: return {}
        # class_ro_t: flags,instStart,instSize,reserved?,ivarLayout,name,baseMethods,...
        flags, iStart, iSize = struct.unpack_from('<III', d, rof)
        nameptr = struct.unpack_from('<I', d, rof+16)[0]
        methptr = struct.unpack_from('<I', d, rof+20)[0]
        cname = self.c._cstr(self.c.v2f(nameptr)) if self.c.v2f(nameptr) else '?'
        meths = self._read_methods(methptr)
        return {cname: meths}

    def _read_methods(self, methptr):
        if not methptr: return []
        d = self.c.d; f = self.c.v2f(methptr)
        if f is None: return []
        entsize, count = struct.unpack_from('<II', d, f)
        out = []
        for i in range(count):
            nameptr, typesptr, imp = struct.unpack_from('<III', d, f + 8 + i*12)
            sel = self.c._cstr(self.c.v2f(nameptr)) if self.c.v2f(nameptr) else '?'
            out.append((sel, imp))
        return out

def disasm(cache, vmaddr, n, symmap=None):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
    from capstone.arm import ARM_OP_REG, ARM_OP_IMM, ARM_OP_MEM
    thumb = vmaddr & 1; a = vmaddr & ~1
    f = cache.v2f(a); code = cache.d[f:f+n*4+32]
    md = Cs(CS_ARCH_ARM, (CS_MODE_THUMB if thumb else 0) | CS_MODE_LITTLE_ENDIAN); md.detail = True
    symmap = symmap or {}
    def read32(va):
        fo = cache.v2f(va)
        if fo is None or fo + 4 > len(cache.d): return None
        return struct.unpack_from('<I', cache.d, fo)[0]
    def cstr(va):
        fo = cache.v2f(va)
        if fo is None: return None
        e = cache.d.find(b'\x00', fo, fo + 160)
        if e < 0: return None
        s = cache.d[fo:e]
        if len(s) < 1 or not all(32 <= b < 127 for b in s): return None
        return s.decode('ascii')
    reg = {}; cnt = 0
    for ins in md.disasm(code, a):
        ops = ins.operands; m = ins.mnemonic; ann = ''
        if m == 'movw' and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            reg[ops[0].reg] = ops[1].imm & 0xffff
        elif m == 'movt' and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            r = ops[0].reg; reg[r] = (reg.get(r, 0) & 0xffff) | ((ops[1].imm & 0xffff) << 16)
        elif m.startswith('add') and len(ops) == 2 and ops[1].type == ARM_OP_REG and ins.reg_name(ops[1].reg) == 'pc':
            r = ops[0].reg
            if r in reg: reg[r] = (reg[r] + ins.address + 4) & 0xffffffff
        elif m.startswith('ldr') and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
            b = ops[1].mem.base; disp = ops[1].mem.disp
            if b and ins.reg_name(b) == 'pc':
                v = read32(((ins.address + 4) & ~3) + disp)
                if v is not None: reg[ops[0].reg] = v
            elif b in reg:
                v = read32(reg[b] + disp)
                if v is not None: reg[ops[0].reg] = v
                else: reg.pop(ops[0].reg, None)
            else: reg.pop(ops[0].reg, None)
        else:
            for o in ops:
                if o.type == ARM_OP_REG and o.access & 2: reg.pop(o.reg, None)  # written
        # annotate the destination register's resolved value
        if ops and ops[0].type == ARM_OP_REG and ops[0].reg in reg:
            v = reg[ops[0].reg]
            if v in symmap: ann = ' ; %s' % symmap[v]
            else:
                s = cstr(v)
                if s and len(s) > 1 and (s[0].islower() or s[0] == '_' or ':' in s): ann = ' ; sel="%s"' % s
                elif s and len(s) > 1: ann = ' ; "%s"' % s
                else:
                    v2 = read32(v); s2 = cstr(v2) if v2 else None
                    if s2 and len(s2) > 1: ann = ' ; ->"%s"' % s2
                    elif v2 in symmap: ann = ' ; ->%s' % symmap[v2]
        print('%#010x  %-8s %-26s%s' % (ins.address, m, ins.op_str, ann))
        cnt += 1
        if cnt >= n: break

def main():
    if len(sys.argv) < 3: raise SystemExit(__doc__)
    cache = Cache(sys.argv[1]); cmd = sys.argv[2]
    if cmd == 'images':
        pat = sys.argv[3] if len(sys.argv) > 3 else ''
        for addr, path in cache.images:
            if pat.lower() in path.lower(): print('%#010x  %s' % (addr, path))
    elif cmd == 'syms':
        addr, path = cache.find_image(sys.argv[3]); mo = MachO(cache, addr)
        pat = sys.argv[4] if len(sys.argv) > 4 else ''
        for name, val in sorted(mo.symbols(), key=lambda x: x[1]):
            if pat in name: print('%#010x  %s' % (val, name))
    elif cmd == 'class':
        addr, path = cache.find_image(sys.argv[3]); mo = MachO(cache, addr)
        want = sys.argv[4]; classes = mo.objc_classes()
        for cname, meths in classes.items():
            if want.lower() in cname.lower():
                print('@interface %s  (%d methods)' % (cname, len(meths)))
                for sel, imp in meths: print('  %#010x  -%s' % (imp, sel))
    elif cmd == 'dis':
        addr, path = cache.find_image(sys.argv[3]); mo = MachO(cache, addr)
        symmap = {v: n for n, v in mo.symbols()}
        va = int(sys.argv[4], 16); n = int(sys.argv[5]) if len(sys.argv) > 5 else 200
        disasm(cache, va, n, symmap)
    else:
        raise SystemExit(__doc__)

if __name__ == '__main__':
    main()
