// Compiler-rt soft-float builtins, hand-provided because the L1ghtmann toolchain
// ships no libclang_rt (REFINDINGS §13). Without these, the compiler emits calls to
// __floatdidf etc. that -Wl,-undefined,dynamic_lookup leaves undefined and defers to
// load-time flat lookup — but they aren't on the iOS-5.1 device (they're meant to be
// statically linked) -> SIGKILL pre-%ctor.
//
// armv7 has VFP, so double/float ADD/SUB/MUL/DIV/CMP and 32-bit int<->fp conversions
// are HARDWARE (vadd/vmul/vdiv/vcvt) — no libcall. Only 64-bit int <-> float/double
// conversions need a libcall (vcvt handles 32-bit only). Each impl below is built ONLY
// from 32-bit conversions + VFP arithmetic, so the compiler can't recurse into a
// builtin it would itself need to provide. __floatdidf was validated on-device.

#define TWO32 4294967296.0           /* 2^32 */

/* int64 -> double */
double __floatdidf(long long a){
    int hi = (int)(a >> 32);            /* signed high half */
    unsigned lo = (unsigned)a;          /* unsigned low half */
    return (double)hi * TWO32 + (double)lo;
}
/* uint64 -> double */
double __floatundidf(unsigned long long a){
    unsigned hi = (unsigned)(a >> 32);
    unsigned lo = (unsigned)a;
    return (double)hi * TWO32 + (double)lo;
}
/* int64 -> float */
float __floatdisf(long long a){ return (float)__floatdidf(a); }
/* uint64 -> float */
float __floatundisf(unsigned long long a){ return (float)__floatundidf(a); }

/* double -> int64 (truncating) */
long long __fixdfdi(double a){
    if (a >= 9223372036854775807.0)  return 0x7fffffffffffffffLL;
    if (a <= -9223372036854775808.0) return (long long)0x8000000000000000ULL;
    int neg = a < 0.0;
    if (neg) a = -a;
    unsigned hi = (unsigned)(a / TWO32);
    unsigned lo = (unsigned)(a - (double)hi * TWO32);
    long long r = ((long long)hi << 32) | (long long)lo;
    return neg ? -r : r;
}
/* float -> int64 */
long long __fixsfdi(float a){ return __fixdfdi((double)a); }

/* double -> uint64 (truncating) */
unsigned long long __fixunsdfdi(double a){
    if (a <= 0.0) return 0ULL;
    if (a >= 18446744073709551615.0) return 0xffffffffffffffffULL;
    unsigned hi = (unsigned)(a / TWO32);
    unsigned lo = (unsigned)(a - (double)hi * TWO32);
    return ((unsigned long long)hi << 32) | (unsigned long long)lo;
}
/* float -> uint64 */
unsigned long long __fixunssfdi(float a){ return __fixunsdfdi((double)a); }
