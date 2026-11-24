"""From-scratch Python Assembler for NK-GPU v1 (32-bit SIMT ISA)."""

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
    "HALT":  0xF,
}

# Register aliases: R0=zero, R1=tid, R2=wid, R3..R7=GPR
REGS = {f"R{i}": i for i in range(8)}
REGS.update({"ZERO": 0, "TID": 1, "WID": 2})


def encode(op: str, rd: str = "R0", rs1: str = "R0", rs2: str = "R0", imm16: int = 0) -> int:
    op_code = OPCODES[op.upper()]
    rd_i = REGS[rd.upper()]
    rs1_i = REGS[rs1.upper()]
    rs2_i = REGS[rs2.upper()]
    return (op_code << 28) | (rd_i << 24) | (rs1_i << 20) | (rs2_i << 16) | (imm16 & 0xFFFF)
