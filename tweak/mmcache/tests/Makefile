CC ?= cc

test: mmcachefile

mmcachefile:
	$(CC) -std=c99 -Wall -D_CRT_SECURE_NO_WARNINGS -o /tmp/test_mmcachefile test_mmcachefile.c && /tmp/test_mmcachefile

.PHONY: test mmcachefile
