export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = mmws
# Tweak.x (Logos, the bridge) + the host-tested pure C (mmws.c/mmws_sm.c) + the CFStream
# transport (mmwsconn.c). No ObjC classes, no float math -> no C++ unwind, no mmbuiltins.
mmws_FILES = Tweak.x mmws.c mmws_sm.c mmwsconn.c mmwsbuiltins.c
# CoreFoundation for CFStream (loaded before our inject, safe). Foundation for NSURL/NSString
# in the hooks. NO UIKit/WebKit — UIWebView/WebScriptObject reached via runtime messaging only.
mmws_FRAMEWORKS = Foundation CoreFoundation
mmws_CFLAGS = -fno-objc-arc -fno-exceptions -Wno-deprecated-declarations -Wno-unused-function
# Two-level namespace; -U the libSystem C funcs the 9.3-SDK tbd omits (flat-bind at load).
mmws_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_memmove -Wl,-U,_memcmp \
               -Wl,-U,_malloc -Wl,-U,_calloc -Wl,-U,_free \
               -Wl,-U,_strlen -Wl,-U,_strcmp -Wl,-U,_strncmp -Wl,-U,_strstr -Wl,-U,_strchr \
               -Wl,-U,_snprintf -Wl,-U,_atoi -Wl,-U,_arc4random \
               -Wl,-U,___memcpy_chk -Wl,-U,___memset_chk -Wl,-U,___snprintf_chk \
               -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard

include $(THEOS)/makefiles/tweak.mk
