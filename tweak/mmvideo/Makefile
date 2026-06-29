export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk
TWEAK_NAME = mmvideo
mmvideo_FILES = Tweak.xm MMTransplantEngine.mm
mmvideo_FRAMEWORKS = AVFoundation CoreMedia QuartzCore UIKit Foundation
mmvideo_CFLAGS = -fobjc-arc -Wno-deprecated-declarations -fno-exceptions
# ObjC++ (.xm/.mm) pulls the C++ personality + memcpy that Theos doesn't auto-link
# here; both exist in the device's loaded libs (libstdc++ in MobileSafari, memcpy in
# libSystem), so resolve them at load.
mmvideo_LDFLAGS = -Wl,-undefined,dynamic_lookup
include $(THEOS)/makefiles/tweak.mk
