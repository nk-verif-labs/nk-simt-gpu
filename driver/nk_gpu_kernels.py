"""Reusable SIMT Kernel Library for NK-GPU v1.2 (divergent shaders & hardware-loop kernels)."""

from nk_gpu_asm import encode


def build_divergent_orb_shader(y: int) -> list[int]:
    """
    32x32 per-lane divergent shader using IF_LT / IF_EQ / ELSE / ENDIF.
    Computes r2 = |x - 16|^2 + |y - 16|^2 per lane and shades 3 divergent regions.
    """
    dy = abs(y - 16)
    dy2 = dy * dy
    return [
        encode("SHL",   rd="R3", rs1="WID", imm16=2),      # R3 = x = (WID << 2) + TID
        encode("ADD",   rd="R3", rs1="R3",  rs2="TID"),
        encode("MOVI",  rd="R4", imm16=16),
        encode("IF_LT", rs1="R3", rs2="R4"),               # if x < 16: dx = 16 - x
        encode("SUB",   rd="R5", rs1="R4",  rs2="R3"),
        encode("ELSE"),                                    # else: dx = x - 16
        encode("SUB",   rd="R5", rs1="R3",  rs2="R4"),
        encode("ENDIF"),
        encode("MUL",   rd="R5", rs1="R5",  rs2="R5"),     # R5 = dx^2
        encode("ADDI",  rd="R5", rs1="R5",  imm16=dy2),    # R5 = r2 = dx^2 + dy^2
        encode("MOVI",  rd="R4", imm16=90),
        encode("IF_LT", rs1="R5", rs2="R4"),               # Region 1: inner orb (r2 < 90)
        encode("SHL",   rd="R6", rs1="R5",  imm16=5),
        encode("MOVI",  rd="R7", imm16=0xF810),
        encode("OR",    rd="R6", rs1="R6",  rs2="R7"),
        encode("ELSE"),
        encode("MOVI",  rd="R4", imm16=196),
        encode("IF_LT", rs1="R5", rs2="R4"),               # Region 2: cyan ring (90 <= r2 < 196)
        encode("MOVI",  rd="R6", imm16=0x07FF),
        encode("ELSE"),                                    # Region 3: checkerboard background
        encode("ADDI",  rd="R7", rs1="R3",  imm16=y),
        encode("MOVI",  rd="R4", imm16=1),
        encode("AND",   rd="R7", rs1="R7",  rs2="R4"),
        encode("IF_EQ", rs1="R7", rs2="ZERO"),
        encode("MOVI",  rd="R6", imm16=0x0008),
        encode("ELSE"),
        encode("MOVI",  rd="R6", imm16=0x218C),
        encode("ENDIF"),
        encode("ENDIF"),
        encode("ENDIF"),
        encode("SETPX", rs1="R3", rs2="R6", imm16=y),
        encode("HALT"),
    ]


def build_matvec4_kernel() -> list[int]:
    """
    4-lane parallel 4x4 matrix-vector multiply: y[row] = sum_{k=0..3} A[row*4 + k] * x[k].
    DMEM[0..15] = A (row-major), DMEM[16..19] = x, DMEM[20..23] = y.
    """
    return [
        encode("SHL",  rd="R3", rs1="TID", imm16=2),   # 0: R3 = row * 4
        encode("MOVI", rd="R4", imm16=0),              # 1: R4 = k = 0
        encode("MOVI", rd="R5", imm16=0),              # 2: R5 = acc = 0
        encode("ADD",  rd="R6", rs1="R3",  rs2="R4"),  # 3: loop body: idx = row*4 + k
        encode("LD",   rd="R6", rs1="R6",  imm16=0),   # 4: R6 = A[row*4 + k]
        encode("LD",   rd="R7", rs1="R4",  imm16=16),  # 5: R7 = x[k]
        encode("MUL",  rd="R6", rs1="R6",  rs2="R7"),  # 6: R6 = A * x
        encode("ADD",  rd="R5", rs1="R5",  rs2="R6"),  # 7: acc += A * x
        encode("ADDI", rd="R4", rs1="R4",  imm16=1),   # 8: k++
        encode("MOVI", rd="R7", imm16=4),              # 9: bound = 4
        encode("BLT",  rs1="R4", rs2="R7", imm16=3),   # 10: if k < 4 goto PC=3
        encode("ST",   rs1="TID", rs2="R5", imm16=20), # 11: DMEM[20 + row] = acc
        encode("HALT"),                                # 12: HALT
    ]
