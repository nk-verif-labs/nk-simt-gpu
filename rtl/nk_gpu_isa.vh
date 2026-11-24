// ============================================================================
// NK-GPU v1 ISA (32-bit Fixed-Width SIMT Instruction Format)
// [31:28] opcode (4b)
// [27:24] rd     (4b: R0..R7)
// [23:20] rs1    (4b: R0..R7)
// [19:16] rs2    (4b: R0..R7)
// [15:0]  imm16  (16b signed/unsigned immediate)
//
// Special Read-Only Registers per Lane:
//   R0 = 0 (always zero)
//   R1 = threadIdx.x (0, 1, 2, 3)
//   R2 = warp_id (set by host before launch)
//   R3..R7 = General-purpose 32-bit registers
// ============================================================================

`define OP_NOP    4'h0
`define OP_MOVI   4'h1  // rd = sign_ext(imm16)
`define OP_ADD    4'h2  // rd = rs1 + rs2
`define OP_SUB    4'h3  // rd = rs1 - rs2
`define OP_MUL    4'h4  // rd = rs1 * rs2 (low 32 bits)
`define OP_ADDI   4'h5  // rd = rs1 + sign_ext(imm16)
`define OP_AND    4'h6  // rd = rs1 & rs2
`define OP_OR     4'h7  // rd = rs1 | rs2
`define OP_SHR    4'h8  // rd = rs1 >> imm16[4:0]
`define OP_SHL    4'h9  // rd = rs1 << imm16[4:0]
`define OP_LD     4'hA  // rd = dmem[(rs1 + imm16[7:0]) & 8'hFF]
`define OP_ST     4'hB  // dmem[(rs1 + imm16[7:0]) & 8'hFF] = rs2
`define OP_SETPX  4'hC  // Emit pixel: x=rs1[7:0], y=imm16[7:0], rgb=rs2[23:0]
`define OP_HALT   4'hF  // Stop warp execution
