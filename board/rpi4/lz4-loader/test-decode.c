/* SPDX-License-Identifier: MIT */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <lz4.h>
int decode(const unsigned char *, size_t, unsigned char *, size_t);
static uint32_t seed = 17;
static unsigned random_byte(void) { seed = seed * 1664525 + 1013904223; return seed >> 24; }
int main(int argc, char **argv)
{
    assert(argc == 2);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    assert(fseek(f, 0, SEEK_END) == 0);
    long size = ftell(f); assert(size > 0); rewind(f);
    unsigned char *raw = malloc(size), *out = malloc(size), *block = malloc(LZ4_compressBound(size));
    assert(raw && out && block); assert(fread(raw, 1, size, f) == (size_t)size); fclose(f);
    int n = LZ4_compress_default((char *)raw, (char *)block, size, LZ4_compressBound(size)); assert(n > 0);
    assert(decode(block, n, out, size) == 0); assert(memcmp(raw, out, size) == 0);
    for (int i = 0; i < 30; ++i) assert(decode(block, (size_t)n * i / 30, out, size) != 0);
    assert(decode(block, n, out, size - 1) != 0);
    for (unsigned trial = 0; trial < 3000; ++trial) {
        unsigned length = (random_byte() << 4) | (random_byte() & 15);
        for (unsigned j = 0; j < length; ++j) raw[j] = trial % 3 ? random_byte() : (j % 7);
        n = LZ4_compress_default((char *)raw, (char *)block, length, LZ4_compressBound(length)); assert(n > 0);
        assert(decode(block, n, out, length) == 0); assert(memcmp(raw, out, length) == 0);
        // Random malformed blocks must stay inside their declared input/output.
        unsigned input = random_byte(), capacity = random_byte();
        unsigned char *bad = malloc(input ? input : 1), *dst = malloc(capacity ? capacity : 1);
        for (unsigned j = 0; j < input; ++j) bad[j] = random_byte();
        (void)decode(bad, input, dst, capacity);
        free(bad); free(dst);
    }
    const unsigned char zero_offset[] = {0, 0, 0};
    assert(decode(zero_offset, sizeof zero_offset, out, 4) != 0);
    free(raw); free(out); free(block);
    puts("Decoder: actual kernel round-trip, truncation, capacity, 3000 randomized cases passed.");
}
