"""本机文件系统适配器（core.ports.FileSystem 的一个实现）。

位置：
    引擎适配层（core 之外）。核心只认 FileSystem 端口，本模块负责真正碰磁盘。

职责：
    - exists / read_text / write_text 三个动作的全部实现细节：编码（UTF-8）、
      自动建父目录、写入采用"先写临时文件再替换"；
    - 不做任何业务判断：不认识存档结构、不解析 JSON、不校验版本（那是 core.persistence 的事）。

为什么写入要先写临时文件再替换：
    直接覆盖时，如果写到一半断电 / 崩溃，原存档就毁了。先写 `<路径>.tmp` 再原子替换，
    最坏情况是留下一个临时文件，原存档仍然完整——崩溃恢复属于将来要做的 F 项，
    但"不写坏原档"这一步现在就可以做到。

草案依据：
    §4 适配层允许引用平台库（核心逻辑层禁止）；P7 端口与适配器；
    §18.1 存档落盘；§19.2 保存失败不回滚 State（失败要如实报出来）。
"""

import os
from pathlib import Path


class LocalFileSystem:
    """用本机路径读写文本文件。

    字段：
        无（本类不保存状态；路径每次调用时给出）。
    """

    def exists(self, path: str) -> bool:
        """判断路径是否存在且是一个文件。

        输入：
            path: 文件路径（相对 / 绝对都行，相对路径按当前工作目录解析）。
        输出：
            True 表示这是一个存在的文件。
        异常：
            TypeError: path 不是字符串。
        变量：
            无。
        """
        _require_path(path)
        return Path(path).is_file()

    def read_text(self, path: str) -> str:
        """读取整个文件（UTF-8；带 BOM 的文件也接受）。

        输入：
            path: 文件路径。
        输出：
            文件文本。
        异常：
            TypeError: path 不是字符串；
            OSError: 文件不存在或读失败（原样抛出，由调用方处理与记录）。
        变量：
            无。
        """
        _require_path(path)
        # utf-8-sig：读到 BOM 会吃掉，读没有 BOM 的文件行为不变。
        # Windows 的记事本 / PowerShell 5.1 很容易写出带 BOM 的 JSON（R6-7）。
        return Path(path).read_text(encoding="utf-8-sig")

    def write_text(self, path: str, text: str) -> None:
        """写入文本文件（UTF-8，自动建父目录，先写临时文件再替换）。

        输入：
            path: 目标路径；
            text: 要写入的文本。
        输出：
            无。
        异常：
            TypeError: path / text 类型不对；
            OSError: 建目录或写失败（原样抛出：§19.2 保存失败要如实报告，不回滚 State）。
        变量：
            target: 目标路径对象；
            temporary: 临时文件路径对象（与目标同目录，替换才是原子的）。
        """
        _require_path(path)
        if not isinstance(text, str):
            raise TypeError(f"写文件需要字符串内容，实际是 {type(text).__name__}")
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)

    def list_files(self, folder: str, suffix: str = "") -> tuple[str, ...]:
        """列出目录里的文件（不含子目录），按路径排序。

        输入：
            folder: 目录路径；
            suffix: 只保留以它结尾的文件；空字符串表示全部。
        输出：
            文件路径元组（含目录前缀，已排序）；目录不存在时给空元组。
        异常：
            TypeError: 参数类型不对。
        变量：
            base: 目录路径对象；
            names: 排好序的文件名列表。

        说明：
            目录不存在返回空元组而不是报错：加载 Mod 时"这个可选目录不存在"
            是正常情况（例如没有 scripts 目录），不该中断加载。
        """
        _require_path(folder)
        if not isinstance(suffix, str):
            raise TypeError(f"suffix 必须是字符串，实际是 {type(suffix).__name__}")
        base = Path(folder)
        if not base.is_dir():
            return ()
        names = [
            item.name
            for item in base.iterdir()
            if item.is_file() and (not suffix or item.name.endswith(suffix))
        ]
        return tuple(str(base / name) for name in sorted(names))

    def list_dirs(self, folder: str) -> tuple[str, ...]:
        """列出目录下的子目录，按路径排序。

        输入：
            folder: 目录路径。
        输出：
            子目录路径元组（已排序）；目录不存在时给空元组。
        异常：
            TypeError: folder 类型不对。
        变量：
            base: 目录路径对象；
            names: 排好序的子目录名列表。
        """
        _require_path(folder)
        base = Path(folder)
        if not base.is_dir():
            return ()
        names = sorted(item.name for item in base.iterdir() if item.is_dir())
        return tuple(str(base / name) for name in names)


def _require_path(path: object) -> None:
    """要求路径是非空字符串。

    输入：
        path: 待检查的路径。
    输出：
        无；通过时直接返回。
    异常：
        TypeError: path 不是字符串；
        ValueError: path 是空字符串。
    变量：
        无。
    """
    if not isinstance(path, str):
        raise TypeError(f"路径必须是字符串，实际是 {type(path).__name__}")
    if not path:
        raise ValueError("路径不能是空字符串")
