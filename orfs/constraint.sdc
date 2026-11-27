create_clock -name clk -period 1000 [get_ports clk]
set_input_delay  200 -clock clk [get_ports {rst_n host_sel* host_addr* host_wdata* host_we}]
set_output_delay 200 -clock clk [all_outputs]
