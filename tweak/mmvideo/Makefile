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
mmvideo_FILES = Tweak.x MMTransplantEngine.m
# NO UIKit: linking it (a heavy framework MobileSafari loads early) into our image at
# Substrate's early inject point trips the dyld load (pre-%ctor SIGKILL). We don't need
# it linked — the layer slot-in reaches the video view's CALayer via QuartzCore + dynamic
# objc_msgSend, never a UIKit symbol. Frameworks match the proven load-gate build.
mmvideo_FRAMEWORKS = AVFoundation CoreMedia QuartzCore Foundation
# -fno-objc-arc-exceptions: we're -fno-exceptions, so ARC needn't emit the SjLj
# exception-safe cleanup landing pads (__Unwind_SjLj_*) — dropping them keeps the
# undefined-symbol set to always-loaded libs (matches the proven load-gate build).
mmvideo_CFLAGS = -fobjc-arc -fno-objc-arc-exceptions -Wno-deprecated-declarations -fno-exceptions
mmvideo_LDFLAGS = -Wl,-undefined,dynamic_lookup
include $(THEOS)/makefiles/tweak.mk
