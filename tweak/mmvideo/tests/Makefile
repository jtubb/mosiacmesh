CC ?= cc

test: mmurl mmretire

mmurl:
	$(CC) -std=c99 -Wall -o /tmp/test_mmurl test_mmurl.c && /tmp/test_mmurl

mmretire:
	$(CC) -std=c99 -Wall -o /tmp/test_mmretire test_mmretire.c && /tmp/test_mmretire

.PHONY: test mmurl mmretire
