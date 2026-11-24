`timescale 1ns / 1ps
`include "nk_gpu_isa.vh"

module nk_gpu_lane #(
    parameter LANE_ID = 0
)(
    input  wire        clk,
    input  wire        rst_n,

    input  wire [15:0] warp_id,
    input  wire        exec_en,
    input  wire [3:0]  opcode,
    input  wire [2:0]  rd,
    input  wire [2:0]  rs1,
    input  wire [2:0]  rs2,
    input  wire [15:0] imm16,

    input  wire [15:0] mem_rdata,
    output wire [15:0] op_a_out,
    output wire [15:0] op_b_out
);
    reg [15:0] gpr [3:7];

    wire [15:0] val_a = (rs1 == 3'd0) ? 16'd0 :
                        (rs1 == 3'd1) ? LANE_ID[15:0] :
                        (rs1 == 3'd2) ? warp_id : gpr[rs1];
    wire [15:0] val_b = (rs2 == 3'd0) ? 16'd0 :
                        (rs2 == 3'd1) ? LANE_ID[15:0] :
                        (rs2 == 3'd2) ? warp_id : gpr[rs2];

    assign op_a_out = val_a;
    assign op_b_out = val_b;

    reg [15:0] alu_res;
    reg        we;

    always @(*) begin
        alu_res = 16'd0;
        we      = 1'b0;
        case (opcode)
            `OP_MOVI: begin alu_res = imm16;                       we = 1'b1; end
            `OP_ADD:  begin alu_res = val_a + val_b;               we = 1'b1; end
            `OP_SUB:  begin alu_res = val_a - val_b;               we = 1'b1; end
            `OP_MUL:  begin alu_res = val_a * val_b;               we = 1'b1; end
            `OP_ADDI: begin alu_res = val_a + imm16;               we = 1'b1; end
            `OP_AND:  begin alu_res = val_a & val_b;               we = 1'b1; end
            `OP_OR:   begin alu_res = val_a | val_b;               we = 1'b1; end
            `OP_SHR:  begin alu_res = val_a >> imm16[3:0];         we = 1'b1; end
            `OP_SHL:  begin alu_res = val_a << imm16[3:0];         we = 1'b1; end
            `OP_LD:   begin alu_res = mem_rdata;                   we = 1'b1; end
            default:  begin alu_res = 16'd0;                       we = 1'b0; end
        endcase
    end

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (i = 3; i <= 7; i = i + 1) gpr[i] <= 16'd0;
        end else if (exec_en && we && (rd >= 3'd3)) begin
            gpr[rd] <= alu_res;
        end
    end
endmodule
