"""logger 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/
运行：在仓库根执行 `python run_tests.py`。
覆盖：级别过滤与 set_level、序号严格递增、时钟注入、出货口（MemorySink / sink=None），
      以及出货口抛异常不拖垮调用方（R4-12）。
"""

import unittest

from core.logger import LogLevel, Logger, MemorySink


class TestLogger(unittest.TestCase):
    """日志的级别、序号与出货口。"""

    def test_info_reaches_the_sink(self):
        """一条 INFO 记录：内容对得上。"""
        sink = MemorySink()
        Logger(sink=sink).info("你好")
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink.records()[0].message, "你好")

    def test_level_filters_lower_records(self):
        """低于最低级别的记录直接丢掉。"""
        sink = MemorySink()
        logger = Logger(sink=sink, level=LogLevel.WARN)
        logger.info("被丢掉")
        self.assertEqual(len(sink), 0)
        logger.warn("留下")
        self.assertEqual(len(sink), 1)

    def test_set_level_changes_filtering(self):
        """set_level 之后按新级别过滤；min_level 读得出来。"""
        sink = MemorySink()
        logger = Logger(sink=sink, level=LogLevel.WARN)
        self.assertEqual(logger.min_level, LogLevel.WARN)
        logger.set_level(LogLevel.TRACE)
        logger.trace("现在能记")
        self.assertEqual(len(sink), 1)

    def test_records_keep_write_order(self):
        """两条记录都进出货口，顺序就是写入顺序（序号由引擎自己保证唯一）。"""
        sink = MemorySink()
        logger = Logger(sink=sink)
        logger.info("一")
        logger.info("二")
        self.assertEqual([record.message for record in sink.records()], ["一", "二"])

    def test_clock_is_injected(self):
        """时钟由调用方注入：给了就读它，不给就恒为 0.0。"""
        sink = MemorySink()
        Logger(sink=sink, clock=lambda: 12.5).info("带时钟")
        self.assertEqual(sink.records()[0].timestamp, 12.5)
        another = MemorySink()
        Logger(sink=another).info("不带时钟")
        self.assertEqual(another.records()[0].timestamp, 0.0)

    def test_sink_none_drops_records(self):
        """sink=None：照常调用、不做任何事（测试里省事）。"""
        logger = Logger(sink=None)
        logger.info("没人接")
        logger.warn("也没人接")

    def test_memory_sink_can_be_cleared(self):
        """MemorySink 的 records / clear / __len__。"""
        sink = MemorySink()
        Logger(sink=sink).info("一条")
        self.assertEqual(len(sink.records()), 1)
        sink.clear()
        self.assertEqual(len(sink), 0)


class TestSinkFailure(unittest.TestCase):
    """R4-12：出货口抛异常不许拖垮调用方。"""

    def test_sink_exception_is_swallowed(self):
        """sink 抛异常时 logger.info 不冒错；连续两次都安全。"""
        class BoomSink:
            """写一次炸一次的出货口。"""

            def write(self, record):
                """直接抛。"""
                raise RuntimeError("磁盘满了")

        logger = Logger(sink=BoomSink())
        logger.info("第一条")
        logger.info("第二条")


if __name__ == "__main__":
    unittest.main()
