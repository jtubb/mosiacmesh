CC ?= cc
CFLAGS ?= -std=c99 -Wall -D_CRT_SECURE_NO_WARNINGS

test: mmcachefile mmrange

mmcachefile:
	$(CC) $(CFLAGS) -o /tmp/test_mmcachefile test_mmcachefile.c && /tmp/test_mmcachefile

mmrange:
	$(CC) $(CFLAGS) -o /tmp/test_mmrange test_mmrange.c && /tmp/test_mmrange

.PHONY: test mmcachefile mmrange
