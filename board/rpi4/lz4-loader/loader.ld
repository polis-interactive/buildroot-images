/* SPDX-License-Identifier: MIT */
ENTRY(_start)
PHDRS { code PT_LOAD FLAGS(5); stack PT_LOAD FLAGS(6); }
SECTIONS {
    . = 0;
    .text : { KEEP(*(.head)) *(.text*) *(.rodata*) *(.iplt) } :code
    .data : { *(.data*) *(.igot*) } :code
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } :code
    .rela : { *(.rela*) } :code
    ASSERT(SIZEOF(.data) == 0, "loader must have no mutable globals")
    ASSERT(SIZEOF(.bss) == 0, "loader must have no BSS")
    ASSERT(SIZEOF(.rela) == 0, "loader must have no runtime relocations")
    .tables : ALIGN(4096) { *(.tables) } :code
    .payload : ALIGN(16) { *(.payload) . = ALIGN(16); } :code
    _file_end = .;
    .stack (NOLOAD) : { . += 8192; } :stack
    _end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
