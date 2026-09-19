/* mmprobe — a load probe for Logos %subclass on iOS 5.1.
 *
 * QUESTION: can this fleet use a RUNTIME ObjC class, and therefore ObjC delegates
 * and KVO observers, at all?
 *
 * BACKGROUND. Two findings have kept every tweak here class-free:
 *   §11  a STATIC ObjC class crashes the dylib load. Bisected 2026-08-04: an EMPTY
 *        static class loads; one carrying a method + ivar still crashes.
 *   §12  the RUNTIME route (objc_allocateClassPair/class_addMethod) is dead because
 *        those symbols "fail to flat-bind".
 *
 * §12 IS REFUTED BY THE SHIPPING BINARY: it named objc_getClass and sel_registerName
 * among the non-binders, and `llvm-nm -u` on the mmcache dylib running on all 24
 * screens lists exactly _objc_getClass / _objc_msgSend / _sel_registerName. §12 was
 * measured under -Wl,-undefined,dynamic_lookup; §14 replaced that with two-level
 * namespace linking and §12 was never re-measured.
 *
 * Logos %subclass builds its class at RUNTIME, so it emits no static class metadata
 * and sidesteps §11 by construction. This probe tests whether that actually loads.
 *
 * TWO ARMS FROM ONE SOURCE. build.sh strips the marked regions to produce the
 * control; it cannot be done with #ifdef because the Logos preprocessor runs BEFORE
 * the C preprocessor and would still emit the subclass scaffolding.
 *   control  : %ctor writes a heartbeat. No class of any kind.
 *   subclass : same, plus a runtime subclass carrying BOTH a method and a property --
 *              the shape a real NSURLConnection delegate needs, and exactly the shape
 *              that killed the load as a STATIC class in the 2026-08-04 bisect --
 *              then instantiates it and exercises both.
 *
 * WHY AN INTERNAL CONTROL: audit 1.16 says a freshly built mmcache does not load at
 * all, for reasons no symbol diff explains. Without a control arm from the same
 * source and toolchain, a failed subclass arm would be unattributable.
 *
 * Methods are called through objc_msgSend casts rather than message syntax. Message
 * syntax needs a compile-time @interface for a class that only exists at runtime,
 * and declaring one would emit an _OBJC_CLASS_$_ reference the linker cannot satisfy.
 * The cast idiom is what mmcache/mmvideo already use throughout.
 *
 * READING THE RESULT (/var/mobile/mmprobe.log, one line per load):
 *   control + subclass both present  -> %subclass WORKS; delegates/KVO are available.
 *   control present, subclass absent -> runtime classes are genuinely blocked.
 *   neither present                  -> the 1.16 load fragility; probe says nothing.
 */
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <stdio.h>

static void mmplog(const char *m) {
    FILE *f = fopen("/var/mobile/mmprobe.log", "a");
    if (f) { fprintf(f, "%s\n", m); fclose(f); }
}

/* PROBE-HOOK-BEGIN */
/* Arm 3 isolates HOOKING. mmcache %hooks WebAppController; the control arm hooks
 * nothing and therefore never links CydiaSubstrate or references MSHookMessageEx.
 * That is the one structural difference between the probe that LOADS and the mmcache
 * build that does not (audit 1.16). If this arm fails, hooking from a fresh build is
 * the trigger; if it loads, hooking is exonerated and the cause is elsewhere in the
 * mmcache source. */
%hook WebAppController
- (void)viewDidLoad {
    %orig;
    mmplog("[mmprobe] HOOK fired: WebAppController viewDidLoad");
}
%end
/* PROBE-HOOK-END */

/* PROBE-SUBCLASS-BEGIN */
/* A runtime subclass with a method AND a property -- deliberately the shape that
 * crashes the load when written as a STATIC class. If this loads, the wall was
 * static class metadata specifically, not ObjC classes as such. */
%subclass MMProbeDelegate : NSObject
%property (nonatomic, assign) long counter;

%new
- (long)bump {
    long n = ((long (*)(id, SEL))objc_msgSend)(self, sel_registerName("counter")) + 1;
    ((void (*)(id, SEL, long))objc_msgSend)(self, sel_registerName("setCounter:"), n);
    return n;
}
%end
/* PROBE-SUBCLASS-END */

%ctor {
    @autoreleasepool {
/* PROBE-HOOKINIT-BEGIN */
        mmplog("[mmprobe] HOOK arm: dylib loaded, ctor running");
        %init;
        return;
/* PROBE-HOOKINIT-END */
/* PROBE-EXERCISE-BEGIN */
        mmplog("[mmprobe] SUBCLASS arm: dylib loaded, ctor running");
        /* Register the default (ungrouped) group. Logos does NOT install hooks or
         * register %subclass classes without this -- the first run of this probe
         * omitted it and %c() correctly returned nil even though the dylib had
         * loaded cleanly. */
        %init;
        {
            /* %c resolves the runtime-created class. Instantiate and exercise the
             * method + property, so we prove the class is USABLE, not merely
             * registered -- registration alone would not prove a delegate works. */
            Class k = %c(MMProbeDelegate);
            if (!k) { mmplog("[mmprobe] SUBCLASS: %c() returned nil -- class NOT registered"); return; }
            id o = ((id (*)(id, SEL))objc_msgSend)(
                       ((id (*)(id, SEL))objc_msgSend)((id)k, sel_registerName("alloc")),
                       sel_registerName("init"));
            if (!o) { mmplog("[mmprobe] SUBCLASS: alloc/init failed"); return; }
            long a = ((long (*)(id, SEL))objc_msgSend)(o, sel_registerName("bump"));
            long b = ((long (*)(id, SEL))objc_msgSend)(o, sel_registerName("bump"));
            char buf[160];
            snprintf(buf, sizeof buf,
                     "[mmprobe] SUBCLASS OK: class=%s bump=%ld,%ld (method + property both work)",
                     class_getName(k), a, b);
            mmplog(buf);
            ((void (*)(id, SEL))objc_msgSend)(o, sel_registerName("release"));
        }
        return;
/* PROBE-EXERCISE-END */
        mmplog("[mmprobe] CONTROL arm: dylib loaded, ctor running (no class of any kind)");
    }
}
