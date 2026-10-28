// Experimental Hermes-KMS -> BGRA frame reader for BOSTO.
// Uses the public Hermes-KMS DRM UAPI; owns the virtual output itself.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/dma-buf.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <drm.h>
#include <drm/hermes_kms_drm.h>

#define XR24 0x34325258U
#define AR24 0x34325241U
static int fd = -1;
static uint8_t *base = NULL;
static size_t bytes = 0;
static int W = 0, H = 0;
static uint64_t frame_seq = 0, cursor_seq = 0, cursor_image_seq = 0;
static uint8_t *cursor_img = NULL;
static int cursor_w = 0, cursor_h = 0;
static struct drm_hermes_kms_acquire_cursor cursor_state;
static int cursor_supported = 0, base_valid = 0;
static char last_error[320] = "not started";

static int fail(const char *msg) {
    snprintf(last_error, sizeof(last_error), "%s: %s", msg, strerror(errno));
    return -1;
}
const char *hb_error(void) { return last_error; }

static int identify_hermes(int f) {
    struct drm_version ver = {0};
    char name[64] = {0};
    ver.name = name;
    ver.name_len = sizeof(name) - 1;
    if (ioctl(f, DRM_IOCTL_VERSION, &ver) < 0) return 0;
    return !strcmp(name, "hermes-kms");
}
static int find_render(void) {
    for (int i = 128; i < 192; i++) {
        char path[64];
        snprintf(path, sizeof(path), "/dev/dri/renderD%d", i);
        int f = open(path, O_RDONLY | O_CLOEXEC);
        if (f < 0) continue;
        if (identify_hermes(f)) return f;
        close(f);
    }
    errno = ENODEV;
    return -1;
}
void hb_close(void) {
    if (fd >= 0) {
        struct drm_hermes_kms_set_output off = {0};
        (void)ioctl(fd, DRM_IOCTL_HERMES_KMS_SET_OUTPUT, &off);
        close(fd);
        fd = -1;
    }
    free(base); base = NULL;
    free(cursor_img); cursor_img = NULL;
    bytes = 0; W = H = 0;
    frame_seq = cursor_seq = cursor_image_seq = 0;
    cursor_w = cursor_h = 0;
    cursor_supported = base_valid = 0;
    memset(&cursor_state, 0, sizeof(cursor_state));
}
int hb_open(int width, int height, int refresh) {
    if (fd >= 0) { errno = EALREADY; return fail("already open"); }
    if (width < 1 || height < 1 || width > 4096 || height > 4096 || refresh < 1 || refresh > 240) {
        errno = EINVAL; return fail("bad display mode");
    }
    W = width; H = height;
    bytes = (size_t)W * H * 4;
    base = malloc(bytes);
    if (!base) { fail("alloc frame"); hb_close(); return -1; }
    fd = find_render();
    if (fd < 0) { fail("Hermes render node not accessible"); hb_close(); return -1; }
    struct drm_hermes_kms_version ver = {0};
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_GET_VERSION, &ver) < 0) { fail("GET_VERSION"); hb_close(); return -1; }
    if (ver.uapi_version < 11) { snprintf(last_error, sizeof(last_error), "Hermes UAPI %u < 11", ver.uapi_version); hb_close(); return -1; }
    struct drm_hermes_kms_caps caps = {0};
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_GET_CAPS, &caps) < 0) { fail("GET_CAPS"); hb_close(); return -1; }
    if (!(caps.flags & HERMES_KMS_CAP_FRAME_WAIT) || !(caps.flags & HERMES_KMS_CAP_DMABUF_EXPORT)) {
        snprintf(last_error, sizeof(last_error), "Hermes missing capture capabilities"); hb_close(); return -1;
    }
    cursor_supported = !!(caps.flags & HERMES_KMS_CAP_CURSOR_CAPTURE);
    struct drm_hermes_kms_set_output mode = {.enabled=1, .width=(uint32_t)W, .height=(uint32_t)H, .refresh_hz=(uint32_t)refresh};
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_SET_OUTPUT, &mode) < 0) {
        fail("SET_OUTPUT (stop hermes-kmsctl hold first)"); hb_close(); return -1;
    }
    snprintf(last_error, sizeof(last_error), "OK: Hermes UAPI %u, cursor=%s", ver.uapi_version, cursor_supported ? "yes" : "no");
    return 0;
}
static int wait_fence(int fence) {
    if (fence < 0) return 0;
    struct pollfd p = {.fd=fence, .events=POLLIN};
    int ret;
    do { ret = poll(&p, 1, 1500); } while (ret < 0 && errno == EINTR);
    if (ret == 0) { errno = ETIMEDOUT; return -1; }
    return ret < 0 ? -1 : 0;
}
static int copy_dmabuf(int dmafd, int fence, uint32_t width, uint32_t height,
                       uint32_t pitch, uint32_t offset, uint8_t *out) {
    if (dmafd < 0 || width == 0 || height == 0 || width > 4096 || height > 4096 ||
        pitch < width * 4 || pitch > 65536) { errno = EINVAL; return -1; }
    if (wait_fence(fence) < 0) return -1;
    size_t length = (size_t)offset + (size_t)pitch * height;
    void *map = mmap(NULL, length, PROT_READ, MAP_SHARED, dmafd, 0);
    if (map == MAP_FAILED) return -1;
    struct dma_buf_sync sync = {.flags=DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
    (void)ioctl(dmafd, DMA_BUF_IOCTL_SYNC, &sync);
    const uint8_t *src = (const uint8_t *)map + offset;
    for (uint32_t y=0; y<height; y++) memcpy(out + (size_t)y*width*4, src+(size_t)y*pitch, width*4);
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
    (void)ioctl(dmafd, DMA_BUF_IOCTL_SYNC, &sync);
    munmap(map, length);
    return 0;
}
static int read_frame(void) {
    struct drm_hermes_kms_acquire_frame f = {0};
    for (int i=0; i<4; i++) f.dma_buf_fd[i] = -1;
    f.sync_file_fd = -1;
    f.flags = HERMES_KMS_FRAME_REQUEST_DMABUF | HERMES_KMS_FRAME_REQUEST_SYNC_FILE;
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_ACQUIRE_FRAME, &f) < 0) {
        if (errno == ESTALE || errno == EAGAIN || errno == ENODATA) return 0;
        return fail("ACQUIRE_FRAME");
    }
    int ok = 0;
    if (!(f.flags & HERMES_KMS_FRAME_DMABUF_VALID) || f.width != (uint32_t)W || f.height != (uint32_t)H ||
        f.format != XR24 || f.modifier != 0 || f.plane_count != 1) {
        snprintf(last_error, sizeof(last_error), "Unsupported frame: %ux%u fmt=0x%08x mod=%llu planes=%u",
                 f.width, f.height, f.format, (unsigned long long)f.modifier, f.plane_count);
        ok = -1;
    } else if (copy_dmabuf(f.dma_buf_fd[0], (f.flags & HERMES_KMS_FRAME_SYNC_FILE_VALID) ? f.sync_file_fd : -1,
                           f.width, f.height, f.pitch[0], f.offset[0], base) < 0) {
        ok = fail("DMA-BUF mmap/copy");
    } else {
        frame_seq = f.sequence;
        base_valid = 1;
        ok = 1;
    }
    for (int i=0; i<4; i++) if (f.dma_buf_fd[i] >= 0) close(f.dma_buf_fd[i]);
    if (f.sync_file_fd >= 0) close(f.sync_file_fd);
    return ok;
}
static int read_cursor(void) {
    if (!cursor_supported) return 0;
    struct drm_hermes_kms_acquire_cursor c = {0};
    for (int i=0; i<4; i++) c.dma_buf_fd[i] = -1;
    c.sync_file_fd = -1;
    c.flags = HERMES_KMS_CURSOR_REQUEST_DMABUF | HERMES_KMS_CURSOR_REQUEST_SYNC_FILE;
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_ACQUIRE_CURSOR, &c) < 0) {
        if (errno == EAGAIN || errno == ENODATA || errno == ESTALE) return 0;
        return fail("ACQUIRE_CURSOR");
    }
    int ok = 0;
    if (c.flags & HERMES_KMS_CURSOR_METADATA_VALID) {
        if (c.image_sequence != cursor_image_seq && (c.flags & HERMES_KMS_CURSOR_BUFFER_VALID) &&
            (c.flags & HERMES_KMS_CURSOR_DMABUF_VALID) && c.width && c.height &&
            c.width <= 1024 && c.height <= 1024 && (c.format == AR24 || c.format == XR24) &&
            c.modifier == 0 && c.plane_count == 1) {
            size_t size = (size_t)c.width * c.height * 4;
            uint8_t *tmp = malloc(size);
            if (!tmp) { errno=ENOMEM; ok=fail("alloc cursor"); goto done; }
            if (copy_dmabuf(c.dma_buf_fd[0], (c.flags & HERMES_KMS_CURSOR_SYNC_FILE_VALID) ? c.sync_file_fd : -1,
                            c.width, c.height, c.pitch[0], c.offset[0], tmp) < 0) {
                free(tmp); ok=fail("cursor DMA-BUF mmap"); goto done;
            }
            free(cursor_img); cursor_img = tmp;
            cursor_w = (int)c.width; cursor_h = (int)c.height;
            cursor_image_seq = c.image_sequence;
        }
        cursor_seq = c.sequence;
        cursor_state = c;  // fd values here are never used after close
        ok = 1;
    }
done:
    for (int i=0; i<4; i++) if (c.dma_buf_fd[i] >= 0) close(c.dma_buf_fd[i]);
    if (c.sync_file_fd >= 0) close(c.sync_file_fd);
    return ok;
}
static void draw_cursor(uint8_t *out) {
    const struct drm_hermes_kms_acquire_cursor *c = &cursor_state;
    if (!cursor_img || !(c->flags & HERMES_KMS_CURSOR_VISIBLE)) return;
    int dx, dy, dw, dh;
    uint32_t sx, sy, sw, sh;
    if (c->flags & HERMES_KMS_CURSOR_GEOMETRY_VALID) {
        dx=c->crtc_x; dy=c->crtc_y; dw=(int)c->crtc_w; dh=(int)c->crtc_h;
        sx=c->src_x; sy=c->src_y; sw=c->src_w; sh=c->src_h;
    } else {
        dx=c->position_x; dy=c->position_y; dw=cursor_w; dh=cursor_h;
        sx=sy=0; sw=(uint32_t)cursor_w<<16; sh=(uint32_t)cursor_h<<16;
    }
    if (dw <= 0 || dh <= 0 || dw > 4096 || dh > 4096) return;
    for (int y=0; y<dh; y++) {
        int yy=dy+y; if (yy<0 || yy>=H) continue;
        int cy=(int)(((uint64_t)sy + (uint64_t)(2*y+1)*sh/(2*(uint32_t)dh))>>16);
        if (cy<0 || cy>=cursor_h) continue;
        for (int x=0; x<dw; x++) {
            int xx=dx+x; if (xx<0 || xx>=W) continue;
            int cx=(int)(((uint64_t)sx + (uint64_t)(2*x+1)*sw/(2*(uint32_t)dw))>>16);
            if (cx<0 || cx>=cursor_w) continue;
            const uint8_t *src=cursor_img+((size_t)cy*cursor_w+cx)*4;
            uint8_t *dst=out+((size_t)yy*W+xx)*4;
            unsigned a=(c->format==XR24) ? 255 : src[3];
            if (a==0) continue;
            if (a==255) { memcpy(dst,src,3); continue; }
            // DRM ARGB8888 cursor plane uses premultiplied alpha.
            for (int ch=0;ch<3;ch++) {
                unsigned v=(unsigned)src[ch]+((unsigned)dst[ch]*(255-a)+127)/255;
                dst[ch]=(uint8_t)(v>255?255:v);
            }
        }
    }
}
int hb_next(void *dst, size_t dst_len, int timeout_ms) {
    if (fd < 0 || !dst || dst_len < bytes) { errno=EINVAL; return fail("hb_next arguments"); }
    if (timeout_ms < 0 || timeout_ms > 3000) timeout_ms=500;
    struct drm_hermes_kms_wait_update wait = {0};
    wait.after_frame_sequence=frame_seq;
    wait.after_cursor_sequence=cursor_seq;
    wait.timeout_ms=(uint32_t)timeout_ms;
    if (ioctl(fd, DRM_IOCTL_HERMES_KMS_WAIT_UPDATE, &wait) < 0) {
        if (errno == ETIMEDOUT || errno == EAGAIN || errno == EINTR) return 0;
        return fail("WAIT_UPDATE");
    }
    int changed=0;
    if (!base_valid || wait.frame_sequence != frame_seq) {
        int ret=read_frame(); if (ret<0) return -1;
        changed |= ret>0;
    }
    if (cursor_supported && (wait.cursor_sequence != cursor_seq || changed)) {
        int ret=read_cursor(); if (ret<0) return -1;
        changed |= ret>0;
    }
    if (!base_valid || !changed) return 0;
    memcpy(dst, base, bytes);
    draw_cursor(dst);
    return 1;
}
