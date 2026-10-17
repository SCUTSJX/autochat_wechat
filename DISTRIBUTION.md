# 分发状态与依赖说明

本包是供验证的分发候选版，尚未选择项目许可证，不标注为已正式开源。当前没有代项目作者授予开源许可，也没有替代法律审查。是否发布、采用何种许可证、版权归属及第三方使用条款，需要权利人确认。

发布前应确认源码及所采用资料的权利来源、第三方依赖许可证和通知要求，以及所使用客户端、模型服务及使用场景的适用条款。本工具不附带微信安装程序、客户端二进制、第三方库副本、个人密钥或账号校准文件。版本适配数据来自本地已有程序记录，仍应在正式发布前审查其分发条件。

Python 直接依赖见 requirements.txt：HTTPX、Frida、Pillow。系统直接依赖由 reply_all.sh install 列出：Python/venv、PyGObject、AT-SPI、GTK/GDK、D-Bus、xinput、X11/XInput/XTest 等。安装时由 Ubuntu 和 Python 包分发渠道提供，各自按其许可证分发；本说明不是完整的第三方许可证清单或法律结论。

选择许可证后再添加权利人认可的 LICENSE，并核实依赖通知；未完成前建议保持私有测试，不直接公开发布。许可证不能替代隐私清理、账号授权或客户端使用条款的核查。

参考：GitHub 关于仓库许可证的说明：https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
