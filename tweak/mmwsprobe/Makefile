export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = mmwsprobe
# Plain ObjC (.x), no ObjC++ / no float math -> no C++ unwind, no compiler-rt builtins
# (mmbuiltins not needed). Matches the mmvideo load-gate model (REFINDINGS §8/§13).
mmwsprobe_FILES = Tweak.x
# Foundation only (pulls libobjc; proven safe in mmvideo). NO UIKit — we reach UIKit/WebKit
# classes purely by runtime name lookup + objc_msgSend, never a linked UIKit symbol, so we
# avoid the heavy-framework early-inject load trip (mmvideo REFINDINGS).
mmwsprobe_FRAMEWORKS = Foundation
mmwsprobe_CFLAGS = -fno-objc-arc -fno-exceptions -Wno-deprecated-declarations -Wno-unused-function
# Two-level namespace; per-symbol -U only for the libSystem C funcs the 9.3-SDK tbd omits
# (they DO flat-bind from libSystem at load). Same discipline as mmvideo.
mmwsprobe_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_free -Wl,-U,_malloc -Wl,-U,_calloc \
                    -Wl,-U,_fopen -Wl,-U,_fclose -Wl,-U,_fprintf -Wl,-U,_vfprintf \
                    -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard

include $(THEOS)/makefiles/tweak.mk
