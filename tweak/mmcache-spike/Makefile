export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = mmcachespike
# Single-file spike bridge. No ObjC classes, no float math -> no C++ unwind, no compiler-rt
# builtins. Foundation only (NSData/NSURL/NSString/NSFileManager reached via objc_msgSend).
mmcachespike_FILES = Tweak.x
mmcachespike_FRAMEWORKS = Foundation
mmcachespike_CFLAGS = -fno-objc-arc -fno-exceptions -Wno-deprecated-declarations -Wno-unused-function
# Two-level namespace; -U the libSystem C funcs the 9.3-SDK tbd omits (flat-bind at load).
mmcachespike_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_memmove -Wl,-U,_memcmp \
               -Wl,-U,_malloc -Wl,-U,_calloc -Wl,-U,_free \
               -Wl,-U,_strlen -Wl,-U,_strcmp -Wl,-U,_strncmp -Wl,-U,_strstr -Wl,-U,_strchr -Wl,-U,_strncpy \
               -Wl,-U,_snprintf -Wl,-U,_vfprintf -Wl,-U,_fopen -Wl,-U,_fclose -Wl,-U,_atoi -Wl,-U,_unlink \
               -Wl,-U,___memcpy_chk -Wl,-U,___memset_chk -Wl,-U,___strncpy_chk -Wl,-U,___snprintf_chk \
               -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard

include $(THEOS)/makefiles/tweak.mk
