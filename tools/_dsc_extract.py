"""Minimal dyld_shared_cache v1 (iOS-5, armv7) reader — RE tool for the mmws WebCore gate hunt.
Gitignored _ prefix. Finds an image in the cache, dumps its Mach-O symbols (LC_SYMTAB) matching
filters, and can carve its __TEXT for disassembly.

Usage:
  python tools/_dsc_extract.py <cache> --image WebCore --sym WebSocket webSocket jsDOMWindow
  python tools/_dsc_extract.py <cache> --image WebCore --carve out.bin   # rebuild segments -> Mach-O-ish
"""
import struct, sys, argparse

ap = argparse.ArgumentParser()
ap.add_argument("cache")
ap.add_argument("--image", default="WebCore")
ap.add_argument("--sym", nargs="*", default=[])
ap.add_argument("--carve", default="")
ap.add_argument("--hexdump", nargs=2, metavar=("VMADDR", "LEN"), default=None)
args = ap.parse_args()

data = open(args.cache, "rb").read()
magic = data[:16].split(b"\0")[0].decode("ascii", "replace")
print(f"cache magic: {magic!r}  size={len(data)}")

mappingOffset, mappingCount, imagesOffset, imagesCount = struct.unpack_from("<IIII", data, 16)
mappings = []  # (address, size, fileOffset)
for i in range(mappingCount):
    a, s, fo, mp, ip = struct.unpack_from("<QQQII", data, mappingOffset + i * 32)
    mappings.append((a, s, fo))

def v2o(vmaddr):
    for a, s, fo in mappings:
        if a <= vmaddr < a + s:
            return fo + (vmaddr - a)
    return None

# find image
img_addr = None; img_path = None
for i in range(imagesCount):
    addr, mt, ino, pathOff, pad = struct.unpack_from("<QQQII", data, imagesOffset + i * 32)
    end = data.index(b"\0", pathOff)
    path = data[pathOff:end].decode("ascii", "replace")
    if args.image in path:
        img_addr, img_path = addr, path
        break
if img_addr is None:
    print(f"image {args.image!r} not found"); sys.exit(1)
print(f"image: {img_path}  vmaddr=0x{img_addr:x}")

if args.hexdump:
    va = int(args.hexdump[0], 0); ln = int(args.hexdump[1], 0)
    o = v2o(va)
    b = data[o:o + ln]
    print(" ".join("0x%02x" % c for c in b))
    sys.exit(0)

hdr = v2o(img_addr)
mh_magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags = struct.unpack_from("<IiiIIII", data, hdr)
print(f"mach-o magic=0x{mh_magic:x} ncmds={ncmds} (armv7 32-bit expects 0xfeedface)")

# walk load commands
off = hdr + 28  # 32-bit mach_header is 28 bytes
segs = []       # (segname, vmaddr, vmsize, fileoff, filesize)
symtab = None   # (symoff, nsyms, stroff, strsize)
for _ in range(ncmds):
    cmd, cmdsize = struct.unpack_from("<II", data, off)
    if cmd == 0x1:  # LC_SEGMENT
        segname = data[off+8:off+24].split(b"\0")[0].decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<IIII", data, off+24)
        segs.append((segname, vmaddr, vmsize, fileoff, filesize))
    elif cmd == 0x2:  # LC_SYMTAB
        symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, off+8)
        symtab = (symoff, nsyms, stroff, strsize)
    off += cmdsize

print("segments:", [(s[0], hex(s[1]), hex(s[3])) for s in segs])

if symtab:
    symoff, nsyms, stroff, strsize = symtab
    print(f"LC_SYMTAB: nsyms={nsyms} symoff=0x{symoff:x} stroff=0x{stroff:x} strsize={strsize}")
    # In the v1 cache, symoff/stroff are cache-file offsets into the shared LINKEDIT.
    matched = 0
    for i in range(nsyms):
        base = symoff + i * 12   # nlist_32: n_strx(I) n_type(B) n_sect(B) n_desc(H) n_value(I)
        if base + 12 > len(data): break
        n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from("<IBBHI", data, base)
        nameoff = stroff + n_strx
        if nameoff >= len(data): continue
        e = data.index(b"\0", nameoff)
        name = data[nameoff:e].decode("ascii", "replace")
        if not args.sym or any(f in name for f in args.sym):
            print(f"  0x{n_value:08x} t=0x{n_type:02x} {name}")
            matched += 1
            if matched > 400: print("  ...(truncated)"); break
    if matched == 0 and args.sym:
        print("  (no symbols matched — likely stripped of locals; only exports present)")

if args.carve:
    out = bytearray()
    for segname, vmaddr, vmsize, fileoff, filesize in segs:
        src = v2o(vmaddr)
        if src is None or filesize == 0: continue
        if len(out) < fileoff + filesize: out.extend(b"\0" * (fileoff + filesize - len(out)))
        out[fileoff:fileoff+filesize] = data[src:src+filesize]
    open(args.carve, "wb").write(out)
    print(f"carved {len(out)} bytes -> {args.carve} (segments at original fileoffs)")
