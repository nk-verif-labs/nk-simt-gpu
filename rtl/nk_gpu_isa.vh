// ============================================================================
// NK-GPU v1.2 ISA (32-bit Fixed-Width SIMT Instruction Format)
// [31:28] opcode (4b)
// [27:24] rd     (4b: R0..R7, or sub-op for BR/SIMT)
// [23:20] rs1    (4b: R0..R7)
// [19:16] rs2    (4b: R0..R7)
// [15:0]  imm16  (16b immediate / branch target pc[4:0])
// ============================================================================

`define OP_NOP    4'h0
`define OP_MOVI   4'h1
`define OP_ADD    4'h2
`define OP_SUB    4'h3
`define OP_MUL    4'h4
`define OP_ADDI   4'h5
`define OP_AND    4'h6
`define OP_OR     4'h7
`define OP_SHR    4'h8
`define OP_SHL    4'h9
`define OP_LD     4'hA
`define OP_ST     4'hB
`define OP_SETPX  4'hC
`define OP_BR     4'hD  // rd=0: JMP, rd=1: BEQ, rd=2: BNE, rd=3: BLT
`define OP_SIMT   4'hE  // rd=0: IF_EQ, rd=1: IF_LT, rd=2: ELSE, rd=3: ENDIF
`define OP_HALT   4'hF
