/* SPDX-License-Identifier: MIT
 * Pi4 firmware EL2 entry only. Never emits PLIS_ENTRY_V1: Linux does that.
 * All labels are PC-relative so the entire loader can relocate itself.
 */
#include "payload.h"
.section .head, "ax"
.global _start
_start:
    b start
    nop
    .quad 0                         // same text_offset as the enclosed Image
    .quad RELOC_OFFSET + _end        // reserve kernel + relocated loader + stack
    .quad KERNEL_FLAGS
    .quad 0, 0, 0
    .ascii "ARM\x64"
    .long 0                         // raw boot only, not an EFI executable
start:
    msr daifset, #15
    mrs x9, CurrentEL
    cmp x9, #8
    b.ne fail
    mrs x9, hcr_el2
    tbnz x9, #34, fail              // no VHE register aliasing
    mrs x21, sctlr_el2
    tbnz x21, #0, fail              // require MMU and D-cache off
    tbnz x21, #2, fail
    tbnz x21, #25, fail             // little-endian entry only
    mrs x22, mair_el2
    mrs x23, tcr_el2
    mrs x24, ttbr0_el2
    mov x28, sp
    adr x19, _start
    mov x20, x0                     // preserve firmware's live DT pointer
    tst x19, #0x1fffff
    b.ne fail
    tst x20, #7
    b.ne fail
    cbz x20, fail
    ldr x9, =RELOC_OFFSET + _end
    add x27, x19, x9                // end of all loader/kernel work memory
    mov x10, #0xc0000000
    cmp x27, x10
    b.hs fail
    cmp x20, x10
    b.hs fail
    ldr w9, [x20]                   // validate FDT header before touching RAM
    ldr w10, =0xedfe0dd0
    cmp w9, w10
    b.ne fail
    ldr w9, [x20, #4]
    rev w9, w9
    cmp w9, #40
    b.lo fail
    mov w10, #0x200000
    cmp w9, w10
    b.hi fail
    add x9, x20, x9
    cmp x20, x27                    // FDT must not overlap any workspace
    b.hs 1f
    cmp x9, x19
    b.hi fail
1:
    // Pi firmware has not used D-cache. Identity-map low RAM as WBWA and
    // 3-4 GiB (Pi4 MMIO) as device memory. Only mapped low RAM is accessed.
    adr x9, page_table
    msr ttbr0_el2, x9
    mov x9, #0xff                   // Attr0 normal WBWA, Attr1 device nGnRnE
    msr mair_el2, x9
    ldr x9, =0x80803520             // RES1; 32-bit VA/PA; 4K; inner WBWA
    msr tcr_el2, x9
    dsb sy
    tlbi alle2
    ic iallu
    dsb sy
    isb
    orr x9, x21, #1
    orr x9, x9, #4
    orr x9, x9, #0x1000
    msr sctlr_el2, x9
    isb

    // Relocate beyond the kernel's effective size, including its BSS.
    ldr x25, =RELOC_OFFSET
    add x25, x19, x25
    mov x9, x19
    mov x10, x25
    ldr x11, =_file_end
    add x11, x19, x11
2:
    ldp x12, x13, [x9], #16
    stp x12, x13, [x10], #16
    cmp x9, x11
    b.lo 2b
    // Make copied instructions/page tables visible before using them.
    mov x0, x25
    mov x1, x10
    bl clean_range
    ic iallu
    dsb sy
    isb
    adr x9, relocated
    sub x9, x9, x19
    add x9, x25, x9
    br x9
relocated:
    adrp x9, page_table
    add x9, x9, :lo12:page_table
    msr ttbr0_el2, x9
    isb
    tlbi alle2
    dsb sy
    isb
    adrp x9, _end
    add x9, x9, :lo12:_end
    mov sp, x9
    adrp x0, payload
    add x0, x0, :lo12:payload
    ldr x1, =COMPRESSED_BYTES
    mov x2, x19
    ldr x3, =KERNEL_BYTES
    bl decode
    cbnz w0, fail

    // No stack writes after this flush. Restore entry state with MMU off,
    // clean kernel bytes to PoC, invalidate I-cache and restore the live DTB.
    mov x0, x19
    mov x1, x27
    bl clean_range
    msr sctlr_el2, x21
    isb
    tlbi alle2
    dsb sy
    ic iallu
    dsb sy
    isb
    msr mair_el2, x22
    msr tcr_el2, x23
    msr ttbr0_el2, x24
    isb
    mov sp, x28
    mov x0, x20
    mov x1, xzr
    mov x2, xzr
    mov x3, xzr
    br x19

// x0..x1 exclusive range; clobbers x0, x9, x10 only. No stack accesses.
clean_range:
    mrs x9, ctr_el0
    ubfx x9, x9, #16, #4
    mov x10, #4
    lsl x10, x10, x9
    sub x9, x10, #1
    bic x0, x0, x9
3:
    dc civac, x0
    add x0, x0, x10
    cmp x0, x1
    b.lo 3b
    dsb sy
    ret
fail:
#ifdef EMULATOR_TEST
    mov x0, #0x18
    adr x1, failure_exit
    hlt #0xf000
#else
    mov x9, #0xfe200000
    add x9, x9, #0x1000
    adr x10, failure_text
    mov w13, #0x100000
4:
    ldrb w11, [x10], #1
    cbz w11, 6f
5:
    subs w13, w13, #1
    b.eq 6f
    ldr w12, [x9, #0x18]
    tbnz w12, #5, 5b
    str w11, [x9]
    b 4b
#endif
6:
    wfe
    b 6b
.balign 8
failure_exit: .quad 0x20026, 2
failure_text: .asciz "\nPLIS_LZ4_FAIL\n"
.ltorg
.section .tables, "a"
.balign 4096
page_table:
    .quad 0x00000000701, 0x00040000701, 0x00080000701
    .quad 0x00400000c0000405          // device MMIO, execute-never
    .zero 4096 - 32
.section .payload, "a"
.balign 16
.global payload
payload:
.incbin "payload.lz4"
