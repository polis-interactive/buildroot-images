/* SPDX-License-Identifier: MIT
 * Bounded raw LZ4 block decoder. No allocation, libc, or speculative overreads.
 * Format: https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Block_format.md
 */
#include <stddef.h>
#include <stdint.h>

typedef uint64_t unaligned_word __attribute__((aligned(1), may_alias));

int decode(const unsigned char *src, size_t size,
           unsigned char *dst, size_t capacity)
{
    size_t in = 0, out = 0;
    while (in < size) {
        unsigned token = src[in++];
        size_t length = token >> 4;
        if (length == 15) {
            unsigned extra;
            do {
                if (in == size) return -1;
                extra = src[in++];
                if (length > capacity || extra > capacity - length) return -1;
                length += extra;
            } while (extra == 255);
        }
        if (length > size - in || length > capacity - out) return -1;
        while (length >= 8) {
            *(unaligned_word *)(dst + out) = *(const unaligned_word *)(src + in);
            in += 8; out += 8; length -= 8;
        }
        while (length--) dst[out++] = src[in++];
        if (in == size) return out == capacity ? 0 : -1;
        if (size - in < 2) return -1;
        size_t offset = src[in] | ((size_t)src[in + 1] << 8);
        in += 2;
        if (!offset || offset > out) return -1;
        length = (token & 15) + 4;
        if (length == 19) {
            unsigned extra;
            do {
                if (in == size) return -1;
                extra = src[in++];
                if (length > capacity || extra > capacity - length) return -1;
                length += extra;
            } while (extra == 255);
        }
        if (length > capacity - out) return -1;
        /* Small offsets intentionally repeat newly written bytes. */
        if (offset >= 8) {
            while (length >= 8) {
                *(unaligned_word *)(dst + out) = *(const unaligned_word *)(dst + out - offset);
                out += 8; length -= 8;
            }
        }
        while (length--) { dst[out] = dst[out - offset]; ++out; }
    }
    return -1;
}
