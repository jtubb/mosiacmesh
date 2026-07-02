export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = mmwsc
mmwsc_FILES = Tweak.x
mmwsc_FRAMEWORKS = Foundation
mmwsc_CFLAGS = -fno-objc-arc -fno-exceptions -Wno-deprecated-declarations -Wno-unused-function
mmwsc_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_free -Wl,-U,_malloc \
                -Wl,-U,_fopen -Wl,-U,_fclose -Wl,-U,_vfprintf -Wl,-U,_snprintf \
                -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard

include $(THEOS)/makefiles/tweak.mk
