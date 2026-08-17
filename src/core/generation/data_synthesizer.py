from typing import Dict, Any, List, Optional, Union
import random
import string
from datetime import datetime
from faker import Faker


class DataSynthesizer:
    def __init__(self, locale: str = "zh_CN", seed: Optional[int] = None):
        """
        初始化数据合成器。

        :param locale: 语言/地区设置，默认中文
        :param seed: 随机种子，设置后每次生成的数据可复现
        """
        self.fake = Faker(locale)
        if seed is not None:
            Faker.seed(seed)
            random.seed(seed)

    # -------------------------------------------------------------------------
    # 静态安全载荷 (作为类属性避免重复创建)
    # -------------------------------------------------------------------------
    SQL_INJECTIONS = [
        "' OR '1'='1",
        "' OR '1'='1' --",
        "' OR '1'='1' /*",
        "'; DROP TABLE users; --",
        "' UNION SELECT NULL, NULL, NULL --",
        "admin' --",
        "1; SELECT * FROM information_schema.tables",
    ]

    XSS_PAYLOADS = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "'><script>alert(document.cookie)</script>",
        "<body onload=alert('XSS')>",
    ]

    # -------------------------------------------------------------------------
    # 基础个人信息
    # -------------------------------------------------------------------------

    def phone_number(self, valid: bool = True) -> str:
        """生成中国手机号"""
        if not valid:
            return "12345"  # 位数不足，明显非法
        return self.fake.phone_number()

    def email(self, valid: bool = True) -> str:
        """生成邮箱地址"""
        if not valid:
            return "test.example.com"  # 缺少 @，非法格式
        return self.fake.email()

    def id_card(self, valid: bool = True) -> str:
        """
        生成中国大陆居民身份证号（18位）。
        """
        if not valid:
            return "123456789012345678"  # 校验位不合法

        # 地区码（扩充更多代表性省份）
        region_codes = [
            "110101", "310101", "440101", "330101", "320101",
            "610101", "420101", "500101", "510101", "350101",
            "430101", "210101", "370101", "130101", "230101",
        ]
        region = random.choice(region_codes)

        # 使用 faker 生成出生日期，确保真实性（处理润月/大小月）
        birth = self.fake.date_of_birth(minimum_age=18, maximum_age=70).strftime("%Y%m%d")

        # 顺序码（奇数男、偶数女）
        seq = f"{random.randint(1, 999):03d}"

        base = region + birth + seq  # 17位

        # 计算校验位
        weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
        check_map = "10X98765432"
        total = sum(int(base[i]) * weights[i] for i in range(17))
        check = check_map[total % 11]

        return base + check

    def name(self) -> str:
        """生成中文姓名"""
        return self.fake.name()

    def address(self) -> str:
        """生成中国地址"""
        return self.fake.address()

    def company(self) -> str:
        """生成公司名称"""
        return self.fake.company()

    # -------------------------------------------------------------------------
    # 日期 / 时间
    # -------------------------------------------------------------------------

    def dates(self) -> Dict[str, str]:
        """
        生成多种有代表性的日期字符串，覆盖正常值、边界值和非法值。
        """
        return {
            "valid_normal":    "2024-06-15",
            "valid_leap_day":  "2024-02-29",   # 2024 是闰年，合法
            "invalid_feb_30":  "2023-02-30",   # 非法日期
            "invalid_month_13":"2023-13-01",   # 月份越界
            "boundary_min":    "1900-01-01",
            "boundary_max":    "9999-12-31",
            "empty":           "",
            "random_valid":    self.fake.date(pattern="%Y-%m-%d"),
        }

    # -------------------------------------------------------------------------
    # 字符串边界值
    # -------------------------------------------------------------------------

    def boundary_string(
        self,
        min_len: int,
        max_len: int,
        charset: Optional[Union[List[str], str]] = None,
    ) -> Dict[str, Any]:
        """
        生成边界长度字符串，支持自定义字符集。

        :param min_len: 最小允许长度
        :param max_len: 最大允许长度
        :param charset: 可选字符类型列表 ['alpha', 'numeric', 'special'] 或直接传入字符池字符串
        """
        if isinstance(charset, str) and charset not in ["alpha", "numeric", "special"]:
            char_pool = charset
        else:
            char_pool = self._build_char_pool(charset if isinstance(charset, list) else [charset] if charset else None)

        def _rand_str(length: int) -> str:
            if length <= 0:
                return ""
            return "".join(random.choices(char_pool, k=length))

        return {
            "min-1": _rand_str(max(0, min_len - 1)),
            "min":   _rand_str(min_len),
            "min+1": _rand_str(min_len + 1),
            "max-1": _rand_str(max_len - 1),
            "max":   _rand_str(max_len),
            "max+1": _rand_str(max_len + 1),
        }

    def _build_char_pool(self, charset: Optional[List[str]]) -> str:
        if not charset:
            return "a"
        pool = ""
        if "alpha" in charset:
            pool += string.ascii_letters
        if "numeric" in charset:
            pool += string.digits
        if "special" in charset:
            pool += string.punctuation
        return pool or "a"

    # -------------------------------------------------------------------------
    # 金额
    # -------------------------------------------------------------------------

    def positive_amounts(self) -> Dict[str, float]:
        """生成典型金额测试值"""
        return {
            "zero":         0.0,
            "min_positive": 0.01,
            "small":        1.00,
            "medium":       999.99,
            "large":        50000.0,
            "max_typical":  99999999.99,
            "negative":     -0.01,   # 非法：负数
        }

    # -------------------------------------------------------------------------
    # 安全测试载荷
    # -------------------------------------------------------------------------

    def sql_injections(self) -> List[str]:
        """常见 SQL 注入攻击载荷"""
        return self.SQL_INJECTIONS

    def xss_payloads(self) -> List[str]:
        """常见 XSS 跨站脚本攻击载荷"""
        return self.XSS_PAYLOADS

    def special_characters(self) -> Dict[str, str]:
        """用于测试输入过滤的特殊字符集"""
        return {
            "html_chars":      "<>&\"'",
            "shell_chars":     "|;&`$(){}",
            "path_traversal":  "../../etc/passwd",
            "null_byte":       "test\x00inject",
            "unicode_special": "\u202e\ufeff\u200b",  # 方向控制符、零宽字符
            "emoji":           "😀🔥💯",
            "long_unicode":    "测试" * 50,
        }

    # -------------------------------------------------------------------------
    # 特定格式字符串
    # -------------------------------------------------------------------------

    def license_plate(self, valid: bool = True) -> str:
        """生成中国大陆车牌号"""
        provinces = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁甘"
        letters = string.ascii_uppercase.replace("I", "").replace("O", "")
        if not valid:
            return "A12345"  # 缺少省份汉字，非法
        province = random.choice(provinces)
        letter = random.choice(letters)
        suffix = "".join(random.choices(letters + string.digits, k=5))
        return province + letter + suffix

    def order_number(self) -> str:
        """生成订单号：当前日期 + 随机后缀"""
        prefix = datetime.now().strftime("%Y%m%d%H%M")
        suffix = "".join(random.choices(string.digits, k=6))
        return prefix + suffix

    def bank_card(self, valid: bool = True) -> str:
        """生成银行卡号（16位）"""
        if not valid:
            return "1234"  # 位数不足
        return self.fake.credit_card_number()

    # -------------------------------------------------------------------------
    # 场景化聚合数据
    # -------------------------------------------------------------------------

    def generate_profile(self) -> Dict[str, Any]:
        """
        生成完整的用户画像数据，用于复杂场景测试。
        """
        return {
            "name": self.name(),
            "phone": self.phone_number(),
            "email": self.email(),
            "id_card": self.id_card(),
            "address": self.address(),
            "company": self.company(),
            "bank_card": self.bank_card()
        }


# =============================================================================
# 简单演示
# =============================================================================
if __name__ == "__main__":
    ds = DataSynthesizer(seed=42)

    print("=== 手机号 ===")
    print("有效:", ds.phone_number(valid=True))
    print("无效:", ds.phone_number(valid=False))

    print("\n=== 邮箱 ===")
    print("有效:", ds.email(valid=True))
    print("无效:", ds.email(valid=False))

    print("\n=== 身份证 ===")
    print("有效:", ds.id_card(valid=True))
    print("无效:", ds.id_card(valid=False))

    print("\n=== 日期 ===")
    for k, v in ds.dates().items():
        print(f"  {k}: {repr(v)}")

    print("\n=== 字符串边界值（长度 2~5，含特殊字符）===")
    for k, v in ds.boundary_string(2, 5, charset=["alpha", "numeric", "special"]).items():
        print(f"  {k} (len={len(v)}): {repr(v)}")

    print("\n=== 金额 ===")
    for k, v in ds.positive_amounts().items():
        print(f"  {k}: {v}")

    print("\n=== SQL 注入载荷 ===")
    for p in ds.sql_injections():
        print(" ", repr(p))

    print("\n=== XSS 载荷 ===")
    for p in ds.xss_payloads():
        print(" ", repr(p))

    print("\n=== 特殊字符 ===")
    for k, v in ds.special_characters().items():
        print(f"  {k}: {repr(v)}")

    print("\n=== 车牌号 ===")
    print("有效:", ds.license_plate(valid=True))
    print("无效:", ds.license_plate(valid=False))

    print("\n=== 订单号 ===")
    print(ds.order_number())

    print("\n=== 银行卡号 ===")
    print("有效:", ds.bank_card(valid=True))
    print("无效:", ds.bank_card(valid=False))

    print("\n=== 完整用户画像 ===")
    profile = ds.generate_profile()
    for k, v in profile.items():
        print(f"  {k}: {v}")