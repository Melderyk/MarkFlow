# 参与贡献

欢迎提交问题、修复和文档改进。开发环境为 Windows x64、Python 3.12；安装及运行步骤见 README。

## 报告问题

请说明软件版本、Windows 版本、输入格式、复现步骤、预期及实际行为。优先使用 示例文件/ 或构造一个可公开的最小文档。截图请遮盖个人路径与内容，不要提交 Token、markflow-settings.json、上传缓存或机密文档。

## 修改与验证

1. 新建分支并修改相应模块。
2. 按 README 安装 requirements-dev.txt；涉及转换或图床协议的变更补充相关测试。
3. 运行 `python -m pytest -q`，需要无界面时设置 `QT_QPA_PLATFORM=offscreen`。
4. 涉及 GUI 或打包时，使用 MarkFlow.spec 构建，并运行 `MarkFlow.exe --self-test`。
5. PR 说明问题、最终行为和已执行的验证，避免包含个人文件。

测试样本生成使用 Windows 自带的微软雅黑和宋体。所有图床测试应使用模拟服务器；生产连通测试只上传明确允许公开的测试图片。

贡献者应有权提交相关内容；提交项目原创代码的贡献按本项目 MIT 许可证提供。
