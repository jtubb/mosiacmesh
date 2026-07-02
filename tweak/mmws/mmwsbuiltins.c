/* mmwsbuiltins.c — hand-provided ARM integer-division builtins.
 * The Theos toolchain ships no libclang_rt for iPad-1/armv7 (mmvideo REFINDINGS §13), and the
 * Cortex-A8 has no hardware integer divide, so clang emits __udivsi3/__umodsi3 for the C files'
 * divisions (e.g. base64's /3). Left undefined they'd fail to flat-bind at load -> pre-%ctor
 * SIGKILL. These pure shift/subtract implementations use no division themselves. */

unsigned int __udivsi3(unsigned int a, unsigned int b) {
    if (b == 0) return 0;
    unsigned int q = 0, r = 0;
    for (int i = 31; i >= 0; i--) {
        r = (r << 1) | ((a >> i) & 1u);
        if (r >= b) { r -= b; q |= (1u << i); }
    }
    return q;
}

unsigned int __umodsi3(unsigned int a, unsigned int b) {
    if (b == 0) return 0;
    unsigned int r = 0;
    for (int i = 31; i >= 0; i--) {
        r = (r << 1) | ((a >> i) & 1u);
        if (r >= b) r -= b;
    }
    return r;
}
