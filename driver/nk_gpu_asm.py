"""From-scratch Python Assembler for NK-GPU v1.2 (32-bit SIMT ISA with branching & predication)."""

OPCODES = {
    "NOP":   0x0,
    "MOVI":  0x1,
    "ADD":   0x2,
    "SUB":   0x3,
    "MUL":   0x4,
    "ADDI":  0x5,
    "AND":   0x6,
    "OR":    0x7,
    "SHR":   0x8,
    "SHL":   0x9,
    "LD":    0xA,
    "ST":    0xB,
    "SETPX": 0xC,
    "BR":    0xD,
    "SIMT":  0xE,
    "HALT":  0xF,
}

BRANCH_SUBOPS = {"JMP": 0, "BEQ": 1, "BNE": 2, "BLT": 3}
SIMT_SUBOPS   = {"IF_EQ": 0, "IF_LT": 1, "ELSE": 2, "ENDIF": 3}

REGS = {f"R{i}": i for i in range(8)}
REGS.update({"ZERO": 0, "TID": 1, "WID": 2})


def encode(op: str, rd: str = "R0", rs1: str = "R0", rs2: str = "R0", imm16: int = 0) -> int:
    op_u = op.upper()
    if op_u in BRANCH_SUBOPS:
        op_code = OPCODES["BR"]
        rd_i = BRANCH_SUBOPS[op_u]
    elif op_u in SIMT_SUBOPS:
        op_code = OPCODES["SIMT"]
        rd_i = SIMT_SUBOPS[op_u]
    else:
        op_code = OPCODES[op_u]
        rd_i = REGS[rd.upper()]
    rs1_i = REGS[rs1.upper()]
    rs2_i = REGS[rs2.upper()]
    return (op_code << 28) | (rd_i << 24) | (rs1_i << 20) | (rs2_i << 16) | (imm16 & 0xFFFF)
