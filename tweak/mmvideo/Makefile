export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk
TWEAK_NAME = mmvideo
mmvideo_FILES = Tweak.xm
mmvideo_FRAMEWORKS = AVFoundation CoreMedia QuartzCore UIKit Foundation
mmvideo_CFLAGS = -fobjc-arc -Wno-deprecated-declarations
include $(THEOS)/makefiles/tweak.mk
