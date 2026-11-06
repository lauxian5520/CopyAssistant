"""Editors for API services and system prompts."""

from dataclasses import replace
from uuid import uuid4

from keyring.errors import KeyringError
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import (
    DEFAULT_PROMPTS,
    MODE_LABELS,
    Mode,
    ServiceProfile,
    Settings,
    SettingsStore,
    completion_url,
)
from credentials import CredentialError, Credentials


class ServiceDialog(QDialog):
    """Edit the settings for one provider profile."""

    def __init__(self, profile: ServiceProfile | None, parent: QWidget) -> None:
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle("编辑服务" if profile else "新增服务")
        self.name_edit = QLineEdit(profile.name if profile else "")
        self.url_edit = QLineEdit(profile.url if profile else "")
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        self.model_edit = QLineEdit(profile.model if profile else "")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("留空保留当前地址的密钥" if profile else "输入 API Key")
        self.images_check = QCheckBox("此模型支持图片")
        self.images_check.setChecked(profile.supports_images if profile else False)
        self.clear_key = QCheckBox("清除已保存的密钥")
        self.clear_key.setVisible(profile is not None)
        form = QFormLayout()
        for label, editor in (
            ("服务名称", self.name_edit),
            ("API URL", self.url_edit),
            ("模型 ID", self.model_edit),
            ("API Key", self.key_edit),
        ):
            editor.setAccessibleName(label)
            form.addRow(label, editor)
        form.addRow(self.images_check)
        form.addRow(self.clear_key)
        buttons = QDialogButtonBox()
        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)
        self.setMinimumWidth(475)

    def accept(self) -> None:
        """Validate the fields before closing the editor."""
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        model = self.model_edit.text().strip()
        try:
            if not name or not model:
                raise ValueError("服务名称和模型 ID 不能为空。")
            completion_url(url)
            if self.key_edit.text().strip() and self.clear_key.isChecked():
                raise ValueError("请输入新密钥，或勾选清除密钥。")
        except ValueError as error:
            QMessageBox.warning(self, "检查服务配置", str(error))
            return
        profile_id = self.profile.id if self.profile else uuid4().hex
        self.profile = ServiceProfile(profile_id, name, url, model, self.images_check.isChecked())
        super().accept()


class SettingsDialog(QDialog):
    """Save service edits and prompts to the shared settings store."""

    def __init__(
        self,
        settings: Settings,
        store: SettingsStore,
        credentials: Credentials,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.credentials = credentials
        self.setWindowTitle("CopyAssistant 设置")
        self.resize(640, 480)
        tabs = QTabWidget()
        tabs.addTab(self.service_page(), "AI 服务")
        tabs.addTab(self.prompt_page(), "提示词")
        close = QPushButton("完成")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(close)

    def service_page(self) -> QWidget:
        """Create the list and service actions."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("每个服务使用独立的 API 地址、模型和密钥。"))
        self.services = QListWidget()
        self.services.setAccessibleName("服务列表")
        self.services.itemDoubleClicked.connect(self.edit_service)
        self.refresh_services()
        layout.addWidget(self.services)
        actions = QHBoxLayout()
        for label, callback in (
            ("新增服务", self.add_service),
            ("编辑服务", self.edit_service),
            ("删除服务", self.delete_service),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        return page

    def prompt_page(self) -> QWidget:
        """Create prompt editors with an explicit save action."""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.language_edit = QLineEdit(self.settings.target_language)
        self.language_edit.setAccessibleName("翻译目标语言")
        form.addRow("翻译目标语言", self.language_edit)
        layout.addLayout(form)
        layout.addWidget(QLabel("翻译提示词中的 {target_language} 会替换为目标语言。"))
        tabs = QTabWidget()
        self.prompt_edits: dict[Mode, QPlainTextEdit] = {}
        for mode, label in MODE_LABELS.items():
            editor = QPlainTextEdit(self.settings.prompts[mode])
            editor.setAccessibleName(label + "提示词")
            self.prompt_edits[mode] = editor
            tabs.addTab(editor, label)
        layout.addWidget(tabs)
        actions = QHBoxLayout()
        defaults = QPushButton("恢复默认提示词")
        defaults.clicked.connect(self.reset_prompts)
        save = QPushButton("保存提示词")
        save.clicked.connect(self.save_prompts)
        actions.addWidget(defaults)
        actions.addWidget(save)
        layout.addLayout(actions)
        self.prompt_status = QLabel()
        layout.addWidget(self.prompt_status)
        return page

    def refresh_services(self, selected: int = 0) -> None:
        """Refresh the service list and keep its selection."""
        self.services.clear()
        for profile in self.settings.profiles:
            self.services.addItem(f"{profile.name}  ·  {profile.model}")
        if self.settings.profiles:
            self.services.setCurrentRow(min(selected, len(self.settings.profiles) - 1))

    def add_service(self) -> None:
        """Add a profile and its optional key."""
        self.change_service(None)

    def edit_service(self) -> None:
        """Edit the selected profile."""
        row = self.services.currentRow()
        if row >= 0:
            self.change_service(row)

    def change_service(self, row: int | None) -> None:
        """Apply the editor result and keep credentials scoped to their URL."""
        profiles = list(self.settings.profiles)
        old = profiles[row] if row is not None else None
        dialog = ServiceDialog(old, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.profile is None:
            return
        profile = dialog.profile
        if row is None:
            row = len(profiles)
            profiles.append(profile)
        else:
            profiles[row] = profile
        active_id = self.settings.active_profile_id or profile.id
        updated = replace(self.settings, profiles=tuple(profiles), active_profile_id=active_id)
        if not self.save_settings(updated):
            return
        self.refresh_services(row)
        key = dialog.key_edit.text().strip()
        try:
            if key:
                self.credentials.save(profile, key)
            elif dialog.clear_key.isChecked():
                self.credentials.delete(profile)
        except (OSError, CredentialError, KeyringError, ValueError):
            QMessageBox.warning(self, "凭据未更新", "服务已保存，但 Windows 凭据更新失败。")
        if old is not None and self.credentials.account(old) != self.credentials.account(profile):
            self.remove_key(old)

    def remove_key(self, profile: ServiceProfile) -> None:
        """Clean up a credential after its service is removed or moved."""
        try:
            self.credentials.delete(profile)
        except (OSError, CredentialError, KeyringError, ValueError):
            QMessageBox.warning(self, "凭据清理失败", "可在 Windows 凭据管理器中移除旧密钥。")

    def delete_service(self) -> None:
        """Remove the selected service after confirmation."""
        row = self.services.currentRow()
        if row < 0:
            return
        profile = self.settings.profiles[row]
        if QMessageBox.question(self, "删除服务", f"删除「{profile.name}」和已保存的密钥？") != (
            QMessageBox.StandardButton.Yes
        ):
            return
        profiles = tuple(item for item in self.settings.profiles if item.id != profile.id)
        active_id = self.settings.active_profile_id
        if active_id == profile.id:
            active_id = profiles[0].id if profiles else ""
        updated = replace(self.settings, profiles=profiles, active_profile_id=active_id)
        if self.save_settings(updated):
            self.refresh_services(row)
            self.remove_key(profile)

    def reset_prompts(self) -> None:
        """Load the default prompts into the editor."""
        for mode, editor in self.prompt_edits.items():
            editor.setPlainText(DEFAULT_PROMPTS[mode])
        self.language_edit.setText(Settings().target_language)
        self.prompt_status.setText("已载入默认提示词，点击保存以应用。")

    def save_prompts(self) -> None:
        """Persist all three prompts and the translation language."""
        prompts = {mode: editor.toPlainText().strip() for mode, editor in self.prompt_edits.items()}
        target = self.language_edit.text().strip()
        if not target or not all(prompts.values()):
            self.prompt_status.setText("目标语言和提示词不能为空。")
            return
        if self.save_settings(replace(self.settings, target_language=target, prompts=prompts)):
            self.prompt_status.setText("提示词已保存。")

    def save_settings(self, updated: Settings) -> bool:
        """Apply settings only after successful persistence."""
        try:
            self.store.save(updated)
        except OSError:
            QMessageBox.warning(self, "保存失败", "无法写入本地设置文件。")
            return False
        self.settings = updated
        return True
