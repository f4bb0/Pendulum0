import socket
import sys
import struct
import time

# 确保控制台输出 UTF-8 编码，避免中文乱码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置区 =================
# 推荐使用 255.255.255.255 进行局域网广播。
# 如果有多网卡导致广播失败，可换成具体的子网广播地址（如 192.168.1.255）或目标设备的具体 IP（如 192.168.1.100）
TARGET_IP = "255.255.255.255"  
TARGET_PORT = 4210            
# ==========================================

# ================= 协议常量定义 =================
CMD_SET_BASIC   = 0x01  # 设置基础参数 (角度, 显示)
CMD_SWING       = 0x02  # 设置摆动参数 (速度, 显示)
CMD_SWING_LIMIT = 0x03  # 设置摆动限位 (最小角度, 最大角度)
DEFAULT_MAGIC   = 0xAA

# ================= 差动摆动配置 =================
DIFF_SWING_START_ID = 1
DIFF_SWING_DEVICE_COUNT = 24
DIFF_SWING_MIN_ANGLE = 30
DIFF_SWING_MAX_ANGLE = 90
MAX_STEP_LIMIT = 5
DIFF_SWING_STEP = 1
DIFF_SWING_DELAY_SEC = 0.08
DIFF_SWING_STAGGER_CYCLES = 20
DIFF_SWING_COLUMN_GROUPS = (
    (1, 5, 9),
    (2, 6, 10),
    (3, 7, 11),
    (4, 8, 12),
    (13, 17, 21),
    (14, 18, 22),
    (15, 19, 23),
    (16, 20, 24),
)
ENABLE_DIFF_SWING_DEMO = False

def build_udp_packet(cmd, start_id, devices_data, magic=DEFAULT_MAGIC):
    """
    构建符合 ESP32 协议的 UDP 数据包
    
    :param cmd: 命令码 (0x01, 0x02, 0x03)
    :param start_id: 起始设备 ID (0-255)
    :param devices_data: 设备数据列表，格式为 [(v1, v2), (v1, v2), ...]
                         支持 1-120 个设备。
                         如果不想改变某个值，请传入 None，会自动转为 0xFFFF (65535)
    :param magic: 魔术字节，默认 0xAA
    :return: 构建好的 bytes 数据包
    """
    if not (1 <= len(devices_data) <= 120):
        raise ValueError("设备数量必须在 1 到 120 之间")
        
    payload = bytearray()
    for v1, v2 in devices_data:
        # 处理 None 值，表示不改变当前值 (协议规定 0xFFFF 为不改变)
        val1 = 0xFFFF if v1 is None else int(v1)
        val2 = 0xFFFF if v2 is None else int(v2)
        
        # 范围保护
        if not (0 <= val1 <= 65535) or not (0 <= val2 <= 65535):
            raise ValueError(f"参数超出 uint16 范围: v1={val1}, v2={val2}")
            
        # 大端序 (Big-Endian) 打包为两个 uint16 (各 2 字节)
        payload.extend(struct.pack('>HH', val1, val2))
        
    payload_len = len(payload)
    
    # 构建包头: Magic(1) + startId(1) + cmd(1) + payloadLen(1)
    header = struct.pack('BBBB', magic, start_id, cmd, payload_len)
    
    # 计算校验和: 前面所有字节之和 mod 256 (与 C++ 中的 uint8_t 累加等效)
    checksum = (sum(header) + sum(payload)) & 0xFF
    
    # 拼接完整数据包
    packet = header + payload + bytes([checksum])
    return packet

def send_control_packet(sock, target_ip, target_port, cmd, start_id, devices_data, magic=DEFAULT_MAGIC):
    """
    构建并发送 UDP 控制数据包
    """
    packet = build_udp_packet(cmd, start_id, devices_data, magic)
    sock.sendto(packet, (target_ip, target_port))
    return packet

def send_raw_hex_packet(sock, target_ip, target_port, hex_string):
    """
    发送原始十六进制字符串数据包（用于快速测试或兼容旧协议）
    """
    data_to_send = bytes.fromhex(hex_string)
    sock.sendto(data_to_send, (target_ip, target_port))
    return data_to_send


def triangular_swing_angle(cycle_index, min_angle, max_angle, step):
    """
    生成一个在 min_angle 和 max_angle 之间往返的三角波角度。
    """
    if step == 0:
        return min_angle
    if step < 0:
        raise ValueError("步长必须大于等于 0")
    if max_angle < min_angle:
        raise ValueError("最大角度必须大于等于最小角度")

    span = max_angle - min_angle
    if span % step != 0:
        raise ValueError("角度跨度必须能被步长整除，才能精确往返")

    steps_to_edge = span // step
    if steps_to_edge == 0:
        return min_angle

    period = steps_to_edge * 2
    phase = cycle_index % period
    if phase <= steps_to_edge:
        return min_angle + phase * step
    return max_angle - (phase - steps_to_edge) * step


def angle_to_display_value(angle, min_angle, max_angle):
    """将角度按当前摆动范围映射为 0-999 的显示值。"""
    if max_angle <= min_angle:
        raise ValueError("最大角度必须大于最小角度")
    ratio = (angle - min_angle) / (max_angle - min_angle)
    return round(max(0.0, min(1.0, ratio)) * 999)


def build_differential_angle_data(cycle_index, device_count, min_angle, max_angle, step, stagger_cycles=1, columns=1, column_groups=None):
    """
    为多个设备构建差动摆动的角度数据。

    1. 未传入 column_groups 时，按设备顺序或按列数 columns 进行错位。
    2. 传入 column_groups 时，按显式列组表进行错位，适合 24 个设备这种
       两个 3x4 面板拼接的布局。
    """
    if device_count < 1:
        raise ValueError("设备数量必须大于 0")
    if stagger_cycles < 0:
        raise ValueError("差动延迟周期必须大于等于 0")
    if columns < 1:
        raise ValueError("列数必须大于 0")

    group_offsets = {}
    if column_groups is not None:
        for group_index, group in enumerate(column_groups):
            for device_id in group:
                device_id = int(device_id)
                if device_id in group_offsets:
                    raise ValueError(f"设备 {device_id} 在 column_groups 中重复出现")
                group_offsets[device_id] = group_index

    devices_data = []
    for device_index in range(device_count):
        device_id = device_index + 1
        if column_groups is not None:
            if device_id not in group_offsets:
                raise ValueError(f"设备 {device_id} 未出现在 column_groups 中")
            device_offset = group_offsets[device_id]
        elif columns > 1:
            device_offset = device_index % columns
        else:
            device_offset = device_index

        effective_cycle = cycle_index - device_offset * stagger_cycles
        if effective_cycle < 0:
            angle = min_angle
        else:
            angle = triangular_swing_angle(effective_cycle, min_angle, max_angle, step)
        display_value = angle_to_display_value(angle, min_angle, max_angle)
        devices_data.append((angle, display_value))
    return devices_data


def run_differential_swing(sock, target_ip, target_port, start_id, device_count, min_angle, max_angle, step, delay_sec=0.5, stagger_cycles=1, columns=1, column_groups=None):
    """
    使用 CMD_SET_BASIC 直接发送角度，实现 1-N 号设备的差动往返摆动。
    """
    cycle_index = 0
    while True:
        devices_data = build_differential_angle_data(
            cycle_index,
            device_count,
            min_angle,
            max_angle,
            step,
            stagger_cycles,
            columns,
            column_groups,
        )
        packet = send_control_packet(sock, target_ip, target_port, CMD_SET_BASIC, start_id, devices_data)

        angle_text = ", ".join(f"{start_id + index}:{angle}" for index, (angle, _) in enumerate(devices_data))
        print(f"[循环 {cycle_index}] {angle_text}")
        print(f"发送 HEX: {packet.hex(' ').upper()}\n")

        cycle_index += 1
        time.sleep(delay_sec)


# ================= 测试与使用示例 =================
if __name__ == "__main__":
    try:
        # 创建 UDP Socket (使用 with 语句确保执行完毕后自动关闭)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            
            # 【核心步骤】开启 Socket 的广播权限 
            # (注：即使后续将 TARGET_IP 改为单播 IP，开启此选项通常也无副作用，且能保证广播模式正常工作)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            print(f"开始向 {TARGET_IP}:{TARGET_PORT} 发送控制指令...\n")

            if ENABLE_DIFF_SWING_DEMO:
                print("正在执行 1-24 号设备差动摆动示例：使用 CMD_SET_BASIC 直接发送角度。")
                print(f"参数: 角度 {DIFF_SWING_MIN_ANGLE}° -> {DIFF_SWING_MAX_ANGLE}°，步长 {DIFF_SWING_STEP}°，延迟 {DIFF_SWING_DELAY_SEC}s，错位 {DIFF_SWING_STAGGER_CYCLES} 个循环，按显式列组分组\n")
                run_differential_swing(
                    udp_socket,
                    TARGET_IP,
                    TARGET_PORT,
                    DIFF_SWING_START_ID,
                    DIFF_SWING_DEVICE_COUNT,
                    DIFF_SWING_MIN_ANGLE,
                    DIFF_SWING_MAX_ANGLE,
                    DIFF_SWING_STEP,
                    DIFF_SWING_DELAY_SEC,
                    DIFF_SWING_STAGGER_CYCLES,
                    1,
                    DIFF_SWING_COLUMN_GROUPS,
                )

            # ---------------------------------------------------------
            # 示例 0: 发送原始 HEX 字符串
            # 验证解析: AA(魔术) 01(ID) 01(命令) 08(长度8) 005A(90) 0063(99) 0000(0) 0000(0) 71(校验和)
            # ---------------------------------------------------------
            # raw_hex = "AA 01 01 08 00 5A 00 63 00 00 00 00 71"
            # pkt0 = send_raw_hex_packet(udp_socket, TARGET_IP, TARGET_PORT, raw_hex)
            # print(f"[示例0] 原始 HEX 广播发送成功！")
            # print(f"发送内容: {pkt0.hex(' ').upper()}")
            # print("-" * 60)

            # ---------------------------------------------------------
            # 示例 1: 控制单个设备 (ID=0)
            # 命令: CMD_SET_BASIC
            # 动作: 设置舵机角度为 90°，数码管显示 123
            # ---------------------------------------------------------
            # data1 = [(90, 123)]  
            # pkt1 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 0, data1)
            # print(f"[示例1] 单设备基础设置 -> 发送 HEX: {pkt1.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 2: 控制多个设备 (ID=0 和 ID=1)
            # 命令: CMD_SWING
            # 动作: 设备0速度设为500(显示不变)，设备1速度设为800(显示456)
            # 技巧: 使用 None 表示不改变该参数 (底层自动转为 0xFFFF)
            # # ---------------------------------------------------------
            # data2 = [
            #     (90, 456),  # 设备 1: v1=500, v2=不改变
            #     (90, 456) ,   # 设备 2: v1=800, v2=456
            #     (90, 456)  ,  # 设备 3: v1=800, v2=456
            #     (90, 456)   , # 设备 4: v1=800, v2=456
            #     (90, 456)   , # 设备 5: v1=800, v2=456
            #     (90, 456)   , # 设备 6: v1=800, v2=456
            #     (90, 456)   , # 设备 7: v1=800, v2=456
            #     (90, 456)   , # 设备 8: v1=800, v2=456
            #     (90, 456)   , # 设备 9: v1=800, v2=456
            #     (90, 456)   ,# 设备 10: v1=800, v2=456
            #     (90, 456)   ,# 设备 11: v1=800, v2=456
            #     (90, 456)   # 设备 12: v1=800, v2=456
            # ]
            # pkt2 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 1, data2)
            # print(f"[示例2] 多设备混合控制 -> 发送 HEX: {pkt2.hex(' ').upper()}")

            # ---------------------------------------------------------
            # 示例 3: 批量控制 10 个设备 (ID=10 到 19)
            # 命令: CMD_SWING_LIMIT
            # 动作: 将所有设备的摆动限位统一设置为 30° 到 90°
            # ---------------------------------------------------------
            # data3 = [(30, 90) for _ in range(10)] 
            # pkt3 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SWING_LIMIT, 10, data3)
            # print(f"[示例3] 批量限位设置 (10个设备) -> 发送 HEX: {pkt3.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 4: 设备 1-12 全部摇摆
            # 命令: CMD_SWING
            # 动作: 将设备 1 到 12 的摆动速度统一设置为 500，数码管显示保持不变
            # ---------------------------------------------------------
            data4 = [( 0, None) for _ in range(24)]
            pkt4 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SWING, 1, data4)
            print(f"[示例4] 1-12 设备全部摇摆 -> 发送 HEX: {pkt4.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 5: 设备 1-16 全部 30° 不动，显示 888
            # 命令: CMD_SET_BASIC
            # 动作: 将设备 1 到 16 的角度统一设置为 30，数码管显示设置为 888（显示值 888）
            # 技巧: 角度使用 30（0-65535 范围内），显示为 3 位数 888
            
            # ---------------------------------------------------------
            #time.sleep(10)
            data5 = [(0, 888) for _ in range(24)]
            pkt5 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 1, data5)
            print(f"[示例5] 1-16 设备全部设为 30° 且显示 888 -> 发送 HEX: {pkt5.hex(' ').upper()}")
            
            
            print("\n✅ 所有指令发送完毕，Socket 已自动安全关闭。")
            
    except PermissionError:
        print("❌ 权限被拒绝：请检查防火墙设置，或尝试以管理员/Root权限运行此脚本。")
    except Exception as e:
        print(f"❌ 发送失败: {e}")