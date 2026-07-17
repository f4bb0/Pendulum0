#include <ESP32Servo.h>
#include <WiFi.h>
#include <WiFiUdp.h>

//
    // 0x01: CMD_SET_BASIC
    // 舵机角度 (0-360)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    // 数字显示 (0-999)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    // 0x02: CMD_SWING
    // 舵机速度 (0-999)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    // 数字显示 (0-999)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    // 0x03: CMD_SWING_LIMIT
    // 舵机限位1 (0-360)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    // 舵机限位2 (0-360)：需要 2 字节 (uint16_t)。如果超出范围，例如65,535，则不改变当前值。
    

    // 固定包头：Magic(1) + 起始ID(1) + 命令码(1) + 长度(1) = 4 字节。
    // 控制内容：每个设备 4 字节。连续排列，数量不设上限，实际因为udp包不宜过长，通常不超过120个。
    // 校验和：Checksum(1) = 1 字节。
    // 总开销：5 字节。

    // 隐式设备id，按照顺序来，从起始id开始

// 0xAA: Magic
// 0x00: Start ID (匹配设备 0)
// 0x01: CMD_SET_BASIC
// 0x04: Payload 长度 4 字节
// 0x00 0x5A: v1 = 90 (设置角度为 90度)
// 0x00 0x63: v2 = 99 (设置显示为 099)
// 0x6C: Checksum (前面字节之和取低8位)

// 当前设备的隐式 ID，用于匹配 UDP 数据包中的目标设备
#define MY_DEVICE_ID    2
#define CMD_SET_BASIC   0x01
#define CMD_SWING       0x02
#define CMD_SWING_LIMIT 0x03
// WiFi 和 UDP 配置
const char* ssid = "fabbo";       // 请修改为你的 WiFi 名称
const char* password = "passpasspasspass"; // 请修改为你的 WiFi 密码
unsigned int localUdpPort = 4210;          // 监听 UDP 端口
// 定义与板子连接的引脚
const int SDI_PIN   = 32;  // 对应板上的 SDI (数据线)
const int SCLK_PIN  = 33;  // 对应板上的 SCLK (时钟线)
const int LOAD_PIN  = 25;  // 对应板上的 LOAD (锁存线)

// 假设这是一共 3 个级联的 74HC595
const int NUM_CHIPS = 3; 

// 这里定义标准的共阴极或共阳极数码管段码表（0-9）
// 注意：因为后面有 ULN2003 驱动芯片反相，或者数码管接法不同，
// 如果显示出来的字形反了，只需要把这里的编码取反（如 ~0x3F）即可喵！
const byte SEG_MAP[] = {
  0x3F, // 0
  0x06, // 1
  0x5B, // 2
  0x4F, // 3
  0x66, // 4
  0x6D, // 5
  0x7D, // 6
  0x07, // 7
  0x7F, // 8
  0x6F  // 9
};

#define PIN_SERVO 27             
Servo servo;

// 控制变量
int currentAngle = 45;    // 当前舵机角度
int direction = 1;        // 摆动方向：1 为增大，-1 为减小
int speed = 0;          // 全局速度变量，范围 0-999
int minAngle = 30;
int maxAngle = 60;

void swing(int pace, int minAngle = 30, int maxAngle = 60);

int displayValue = 0;     // 0-999 的原始显示值
int digit3 = 0, digit2 = 0, digit1 = 0;

// UDP 对象与缓冲区
WiFiUDP udp;
uint8_t udpBuffer[512]; 

void setup() {
  Serial.begin(115200);
    // 将控制引脚全部设置为输出模式
  pinMode(SDI_PIN, OUTPUT);
  pinMode(SCLK_PIN, OUTPUT);
  pinMode(LOAD_PIN, OUTPUT);
  
  // 初始化引脚状态
  digitalWrite(SDI_PIN, LOW);
  digitalWrite(SCLK_PIN, LOW);
  digitalWrite(LOAD_PIN, LOW);

  servo.setPeriodHertz(50);
  servo.attach(PIN_SERVO, 500, 2500);

  // 连接 WiFi
  Serial.printf("Connecting to %s ", ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected! IP address: %s\n", WiFi.localIP().toString().c_str());

  // 启动 UDP 监听
  udp.begin(localUdpPort);
  Serial.printf("Now listening at IP %s, UDP port %d\n", WiFi.localIP().toString().c_str(), localUdpPort);
}

void loop() {
 // 处理 UDP 接收
  int packetSize = udp.parsePacket();
  if (packetSize) {
    int len = udp.read(udpBuffer, sizeof(udpBuffer));
    if (len > 0) {
      handleUdpPacket(udpBuffer, len);
    }
  }
  // servo.write(90);
  // delay(1000);
  // servo.write(0);
  // delay(1000);

  // 举个例子：让三个数码管分别显示 1, 2, 3
  // displayThreeDigits(1, 2, 3); //效果是321

  // 显示
  displayThreeDigits(digit3, digit2, digit1);  // 从右到左反向传递，digit1是百位，digit2是十位，digit3是个位
  // 舵机根据全局速度变量摆动
  //手动更新角度
  servo.write(currentAngle);
  swing(speed, minAngle, maxAngle);
}

void updateDisplayValue(int val) {
  displayValue = constrain(val, 0, 999);
  digit1 = displayValue / 100;         // 百位
  digit2 = (displayValue / 10) % 10;   // 十位
  digit3 = displayValue % 10;          // 个位
}


/**
 * 纯软件控制级联数码管显示的核心函数
 */
void displayThreeDigits(int digit1, int digit2, int digit3) {
  // 1. 准备要发送的 3 个字节数据
  // 级联原理：最先发的数据会被后面的数据“推”到最远端的芯片 (U6)
  // 假设从左到右数码管依次为 U6, U4, U1：
  byte dataLeft   = SEG_MAP[digit1]; // 发给最左边数码管 (U6)
  byte dataMiddle = SEG_MAP[digit2]; // 发给中间数码管 (U4)
  byte dataRight  = SEG_MAP[digit3]; // 发给最右边数码管 (U1)

  // 2. 开始发送前，拉低锁存引脚（LOAD），告诉 595 别瞎动
  digitalWrite(LOAD_PIN, LOW);

  // 3. 纯软件串行输出数据 (使用标准 MSBFIRST 高位先出)
  // 先发最左边的数据，它会被后续的数据一路顶到最深处
  shiftOut(SDI_PIN, SCLK_PIN, MSBFIRST, dataLeft);
  shiftOut(SDI_PIN, SCLK_PIN, MSBFIRST, dataMiddle);
  shiftOut(SDI_PIN, SCLK_PIN, MSBFIRST, dataRight);

  // 4. 数据全部发送完毕，各就各位！
  // 给 LOAD 一个高电平脉冲，锁存数据，数码管瞬间刷新显示
  digitalWrite(LOAD_PIN, HIGH);
  delayMicroseconds(5); // 稍微保持一下，确保芯片识别
  digitalWrite(LOAD_PIN, LOW);
}

/**
 * 舵机摆动函数（支持可配置边界）
 * 在 minAngle..maxAngle 范围内细分运动
 * @param pace     速度参数，范围 0-999，值越大摆动越快
 *                 0 表示不动，999 表示最快速度
 * @param minAngle 最小角度（默认为 30）
 * @param maxAngle 最大角度（默认为 60）
 */
void swing(int pace, int minAngle, int maxAngle) {
  // 如果速度为 0，则不移动（保持当前角度和方向）
  if (pace <= 0) {
    return;
  }
  // 保证 minAngle <= maxAngle
  if (minAngle > maxAngle) {
    int t = minAngle;
    minAngle = maxAngle;
    maxAngle = t;
  }

  // 如果当前角度不在边界内，先约束到边界范围并重置方向
  if (currentAngle < minAngle) {
    currentAngle = minAngle;
    direction = 1;
  } else if (currentAngle > maxAngle) {
    currentAngle = maxAngle;
    direction = -1;
  }

  // 将 0-999 的输入映射到 0-15 的角度步长（保持原有响应特性）
  int step = map(pace, 0, 999, 0, 15);

  // 更新当前角度
  currentAngle += direction * step;

  // 检查边界并调整方向
  if (currentAngle >= maxAngle) {
    currentAngle = maxAngle;
    direction = -1;
  } else if (currentAngle <= minAngle) {
    currentAngle = minAngle;
    direction = 1;
  }

  // 设置舵机角度
  servo.write(currentAngle);
}

// ================= UDP 协议解析 =================

static uint16_t readUint16BE(const uint8_t *buf, int idx) {
  return ((uint16_t)buf[idx] << 8) | (uint16_t)buf[idx + 1];
}

void handleUdpPacket(uint8_t *buf, int len) {
  if (len < 5) return; 

  uint8_t magic = buf[0];
  uint8_t startId = buf[1];
  uint8_t cmd = buf[2];
  uint8_t payloadLen = buf[3];

  int expected = 4 + payloadLen + 1; 
  if (len != expected) {
    Serial.printf("UDP size mismatch: got=%d expected=%d\n", len, expected);
    return;
  }

  // 校验和检查
  uint8_t sum = 0;
  for (int i = 0; i < len - 1; ++i) sum += buf[i];
  if (sum != buf[len - 1]) {
    Serial.printf("UDP checksum fail: sum=0x%02X chk=0x%02X\n", sum, buf[len - 1]);
    return;
  }

  // 遍历每个设备的数据块 (每块 4 字节)
  int devCount = payloadLen / 4;
  for (int i = 0; i < devCount; ++i) {
    int off = 4 + i * 4;
    uint16_t v1 = readUint16BE(buf, off);
    uint16_t v2 = readUint16BE(buf, off + 2);
    uint8_t devId = startId + i;

    // 只处理属于当前设备 ID 的数据
    if (devId != MY_DEVICE_ID) continue;

    switch (cmd) {
      case CMD_SET_BASIC:
        if (v1 != 0xFFFF && v1 <= 180) {
          currentAngle = constrain((int)v1, 0, 360);
          Serial.printf("[DEV %d] SET_BASIC angle=%d\n", devId, currentAngle);
        }
        if (v2 != 0xFFFF && v2 <= 999) {
          updateDisplayValue(v2);
          Serial.printf("[DEV %d] SET_BASIC display=%d\n", devId, displayValue);
        }
        break;
        
      case CMD_SWING:
        if (v1 != 0xFFFF && v1 <= 999) {
          speed = (int)v1;
          Serial.printf("[DEV %d] SWING speed=%d\n", devId, speed);
        }
        if (v2 != 0xFFFF && v2 <= 999) {
          updateDisplayValue(v2);
          Serial.printf("[DEV %d] SWING display=%d\n", devId, displayValue);
        }
        break;
        
      case CMD_SWING_LIMIT:
        if (v1 != 0xFFFF && v1 <= 360) {
          minAngle = (int)v1;
          Serial.printf("[DEV %d] LIMIT min=%d\n", devId, minAngle);
        }
        if (v2 != 0xFFFF && v2 <= 360) {
          maxAngle = (int)v2;
          Serial.printf("[DEV %d] LIMIT max=%d\n", devId, maxAngle);
        }
        // 自动纠正大小关系
        if (minAngle > maxAngle) {
          int t = minAngle; minAngle = maxAngle; maxAngle = t;
        }
        break;
        
      default:
        Serial.printf("Unknown cmd: 0x%02X\n", cmd);
        break;
    }
  }
}