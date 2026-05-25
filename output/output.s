	.section	__TEXT,__text,regular,pure_instructions
	.build_version macos, 15, 0
	.globl	_main                           ; -- Begin function main
	.p2align	2
_main:                                  ; @main
; %bb.0:                                ; %for.body.endif.3
	sub	sp, sp, #32
	stp	x29, x30, [sp, #16]             ; 16-byte Folded Spill
	mov	x9, sp
	adrp	x8, l_.str.0@PAGE
	add	x8, x8, l_.str.0@PAGEOFF
	str	x8, [x9]
	adrp	x0, l_.str.5@PAGE
	add	x0, x0, l_.str.5@PAGEOFF
	bl	_printf
	mov	x9, sp
	adrp	x8, l_.str.1@PAGE
	add	x8, x8, l_.str.1@PAGEOFF
	str	x8, [x9]
	adrp	x0, l_.str.4@PAGE
	add	x0, x0, l_.str.4@PAGEOFF
	str	x0, [sp, #8]                    ; 8-byte Folded Spill
	bl	_printf
	ldr	x0, [sp, #8]                    ; 8-byte Folded Reload
	mov	x9, sp
	adrp	x8, l_.str.2@PAGE
	add	x8, x8, l_.str.2@PAGEOFF
	str	x8, [x9]
	bl	_printf
	ldr	x0, [sp, #8]                    ; 8-byte Folded Reload
	mov	x9, sp
	adrp	x8, l_.str.3@PAGE
	add	x8, x8, l_.str.3@PAGEOFF
	str	x8, [x9]
	bl	_printf
	mov	x9, sp
	mov	w8, #3                          ; =0x3
                                        ; kill: def $x8 killed $w8
	str	x8, [x9]
	adrp	x0, l_.str.6@PAGE
	add	x0, x0, l_.str.6@PAGEOFF
	bl	_printf
	mov	x8, sp
	mov	x9, #2684354560                 ; =0xa0000000
	movk	x9, #2457, lsl #32
	movk	x9, #16495, lsl #48
	fmov	d0, x9
	str	d0, [x8]
	adrp	x0, l_.str.7@PAGE
	add	x0, x0, l_.str.7@PAGEOFF
	bl	_printf
	ldp	x29, x30, [sp, #16]             ; 16-byte Folded Reload
	add	sp, sp, #32
	ret
                                        ; -- End function
	.section	__TEXT,__const
l_.str.0:                               ; @.str.0
	.asciz	"Li ping"

l_.str.1:                               ; @.str.1
	.asciz	"Wang ming"

l_.str.2:                               ; @.str.2
	.asciz	"Zhang san"

l_.str.3:                               ; @.str.3
	.asciz	"Li si"

l_.str.4:                               ; @.str.4
	.asciz	"%s success\n"

l_.str.5:                               ; @.str.5
	.asciz	"%s fail\n"

	.p2align	4, 0x0                          ; @.str.6
l_.str.6:
	.asciz	"success count = %d \n"

	.p2align	4, 0x0                          ; @.str.7
l_.str.7:
	.asciz	"total score = %.2f \n"

.subsections_via_symbols
