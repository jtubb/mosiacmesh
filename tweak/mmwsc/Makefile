export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = mmwsc
# Tweak.x (exposure + transplant) + the host-tested RFC-6455 stack (shared with tweak/mmws).
mmwsc_FILES = Tweak.x mmws.c mmws_sm.c mmwsconn.c mmwsbuiltins.c
mmwsc_FRAMEWORKS = Foundation CoreFoundation
mmwsc_CFLAGS = -fno-objc-arc -fno-exceptions -Wno-deprecated-declarations -Wno-unused-function
mmwsc_LDFLAGS = -Wl,-U,_memset -Wl,-U,_memcpy -Wl,-U,_memmove -Wl,-U,_memcmp \
                -Wl,-U,_malloc -Wl,-U,_calloc -Wl,-U,_free \
                -Wl,-U,_strlen -Wl,-U,_strcmp -Wl,-U,_strncmp -Wl,-U,_strstr -Wl,-U,_strchr \
                -Wl,-U,_snprintf -Wl,-U,_atoi -Wl,-U,_arc4random \
                -Wl,-U,_fopen -Wl,-U,_fclose -Wl,-U,_vfprintf \
                -Wl,-U,___memcpy_chk -Wl,-U,___memset_chk -Wl,-U,___snprintf_chk \
                -Wl,-U,___stack_chk_fail -Wl,-U,___stack_chk_guard

include $(THEOS)/makefiles/tweak.mk
