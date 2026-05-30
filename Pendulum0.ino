#include <ESP32Servo.h>
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

// 摆动控制变量
int currentAngle = 45;    // 当前舵机角度
int direction = 1;        // 摆动方向：1 为增大，-1 为减小
int speed = 500;          // 全局速度变量，范围 0-999

void setup() {
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
}

void loop() {

  // servo.write(90);
  // delay(1000);
  // servo.write(0);
  // delay(1000);

  // 举个例子：让三个数码管分别显示 1, 2, 3
  // displayThreeDigits(1, 2, 3);
  // delay(1000); 
  
  // // 循环闪烁或者计数
  // for(int i = 0; i <= 9; i++) {
  //   displayThreeDigits(i, i, i); // 三个数字同步显示 i
  //   delay(500);
  // }

  // 显示当前速度（0-999 三位数）
  int digit1 = speed / 100;           // 百位
  int digit2 = (speed / 10) % 10;     // 十位
  int digit3 = speed % 10;            // 个位
  displayThreeDigits(digit3, digit2, digit1);  // 从右到左反向传递
  
  // 舵机根据全局速度变量摆动
  swing(speed);
  delay(50);
  
  // 每次循环递增速度，到999后归0
  speed++;
  if (speed > 999) {
    speed = 0;
  }
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
 * 舵机摆动函数
 * 在 45°±15° 范围内（30° 到 60°）细分运动
 * @param pace 速度参数，范围 0-999，值越大摆动越快
 *            0 表示不动，999 表示最快速度
 */
void swing(int pace) {
  // 将 0-999 的输入映射到 0-15 的角度步长
  int step = map(pace, 0, 999, 0, 15);
  
  // 更新当前角度
  currentAngle += direction * step;
  
  // 检查边界，改变方向
  if (currentAngle >= 60) {
    currentAngle = 60;
    direction = -1;  // 开始向下摆
  } else if (currentAngle <= 30) {
    currentAngle = 30;
    direction = 1;   // 开始向上摆
  }
  
  // 设置舵机角度
  servo.write(currentAngle);
}
