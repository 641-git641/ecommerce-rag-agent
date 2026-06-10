"""Prompt管理模块：负责加载和管理提示词模板

支持从Markdown文件中加载prompt模板，使用占位符替换实现动态内容填充。
"""

import os
from typing import Dict


class PromptManager:
    """Prompt管理器：加载和管理提示词模板
    
    支持从文件加载prompt模板，通过占位符替换实现动态内容填充。
    """

    def __init__(self, prompts_dir: str = "prompts"):
        """
        初始化Prompt管理器
        
        Args:
            prompts_dir: prompt模板文件所在目录，默认为"prompts"
        """
        self.prompts_dir = prompts_dir
        self.templates = {}
        
        # 确保目录存在
        if not os.path.exists(prompts_dir):
            os.makedirs(prompts_dir)

    def load_prompt(self, template_name: str) -> str:
        """
        从文件加载prompt模板
        
        Args:
            template_name: 模板名称（不含扩展名）
            
        Returns:
            模板内容字符串
            
        Raises:
            FileNotFoundError: 模板文件不存在时抛出
        """
        # 优先查找缓存
        if template_name in self.templates:
            return self.templates[template_name]
        
        # 从文件加载
        file_path = os.path.join(self.prompts_dir, f"{template_name}.md")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Prompt模板文件不存在: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 缓存模板
        self.templates[template_name] = content
        return content

    def render_prompt(self, template_name: str, **kwargs) -> str:
        """
        加载模板并替换占位符
        
        Args:
            template_name: 模板名称（不含扩展名）
            **kwargs: 占位符键值对
            
        Returns:
            渲染后的完整prompt字符串
        """
        template = self.load_prompt(template_name)
        
        # 替换所有占位符
        for key, value in kwargs.items():
            placeholder = f"{{% {key} %}}"
            template = template.replace(placeholder, str(value))
        
        return template

    def list_templates(self) -> list:
        """
        获取所有可用的模板列表
        
        Returns:
            模板名称列表（不含扩展名）
        """
        templates = []
        if os.path.exists(self.prompts_dir):
            for filename in os.listdir(self.prompts_dir):
                if filename.endswith('.md'):
                    templates.append(os.path.splitext(filename)[0])
        return templates

    def reload_templates(self):
        """
        重新加载所有模板（清空缓存）
        """
        self.templates = {}


# 创建全局实例
prompt_manager = PromptManager()