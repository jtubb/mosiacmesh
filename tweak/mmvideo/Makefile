export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk
TWEAK_NAME = mmvideo
# PLAIN ObjC (.x/.m), NOT ObjC++ (.xm/.mm): on iOS-5.1 dyld cannot flat-bind the C++
# SjLj unwind symbols (___gxx_personality_sj0/__Unwind_SjLj_*) that ObjC++ pulls in —
# they resolve to libstdc++, which WebKit loads LAZILY (after Substrate injects us) =>
# pre-%ctor SIGKILL. Plain ObjC keeps ARC (libobjc personality, always loaded) and
# links AVFoundation fine (load-gate proven on-device 2026-06-29). See REFINDINGS §8.
# mmbuiltins.c hand-provides the compiler-rt int64<->fp conversion builtins the
# toolchain ships no libclang_rt for (REFINDINGS §13) — without them the load SIGKILLs.
mmvideo_FILES = Tweak.x MMTransplantEngine.m mmbuiltins.c
# NO UIKit: linking it (a heavy framework MobileSafari loads early) into our image at
# Substrate's early inject point trips the dyld load (pre-%ctor SIGKILL). We don't need
# it linked — the layer slot-in reaches the video view's CALayer via QuartzCore + dynamic
# objc_msgSend, never a UIKit symbol. Frameworks match the proven load-gate build.
mmvideo_FRAMEWORKS = AVFoundation CoreMedia QuartzCore Foundation
# -fno-objc-arc-exceptions: we're -fno-exceptions, so ARC needn't emit the SjLj
# exception-safe cleanup landing pads (__Unwind_SjLj_*) — dropping them keeps the
# undefined-symbol set to always-loaded libs (matches the proven load-gate build).
mmvideo_CFLAGS = -fobjc-arc -fno-objc-arc-exceptions -Wno-deprecated-declarations -fno-exceptions
# TWO-LEVEL namespace (NOT dynamic_lookup): binds objc/CoreMedia/Foundation/Substrate
# symbols to their specific dylib at link time. dynamic_lookup deferred them to flat
# load-time lookup where some present-on-device symbols don't resolve -> SIGKILL
# (REFINDINGS §13). Compiler-rt builtins come from mmbuiltins.c. Only the libSystem C
# funcs the 9.3-SDK tbd omits get per-symbol -U (they DO flat-bind from libSystem).
mmvideo_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_memmove -Wl,-U,_calloc -Wl,-U,_free -Wl,-U,_strlen -Wl,-U,_strncmp -Wl,-U,_strpbrk -Wl,-U,_strstr -Wl,-U,___memcpy_chk -Wl,-U,___memset_chk -Wl,-U,___snprintf_chk -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard
include $(THEOS)/makefiles/tweak.mk
