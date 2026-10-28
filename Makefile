CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror=implicit-function-declaration

.PHONY: all clean check-deps
all: libhermes_bosto.so

check-deps:
	@test -f /usr/include/drm/hermes_kms_drm.h || { echo 'Missing Hermes UAPI header: /usr/include/drm/hermes_kms_drm.h'; exit 1; }
	@test -f /usr/include/libdrm/drm.h || { echo 'Missing libdrm headers: sudo pacman -S libdrm'; exit 1; }

libhermes_bosto.so: hermes_capture.c
	$(CC) $(CFLAGS) -std=gnu11 -fPIC -shared -I/usr/include/libdrm -o $@ $<

clean:
	rm -f libhermes_bosto.so
