CC ?= cc
CFLAGS ?= -std=c99 -Wall -D_CRT_SECURE_NO_WARNINGS

test: mmcachefile mmrange mmresume mminflight mmchunk

mmcachefile:
	$(CC) $(CFLAGS) -o /tmp/test_mmcachefile test_mmcachefile.c && /tmp/test_mmcachefile

mmrange:
	$(CC) $(CFLAGS) -o /tmp/test_mmrange test_mmrange.c && /tmp/test_mmrange

mmresume:
	$(CC) $(CFLAGS) -o /tmp/test_mmresume test_mmresume.c && /tmp/test_mmresume

mminflight:
	$(CC) $(CFLAGS) -o /tmp/test_mminflight test_mminflight.c && /tmp/test_mminflight

mmchunk:
	$(CC) $(CFLAGS) -o /tmp/test_mmchunk test_mmchunk.c && /tmp/test_mmchunk

.PHONY: test mmcachefile mmrange mmresume mminflight mmchunk
