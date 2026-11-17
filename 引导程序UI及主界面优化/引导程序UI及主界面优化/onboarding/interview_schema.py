# -*- coding: utf-8 -*-
"""
访谈维度与字段定义。
改问题、加字段只动这个文件，不动状态机。
"""

DIMENSIONS = [
    {
        "id": "behavior",
        "label": "行为空间",
        "fields": [
            {
                "id": "behavior_space",
                "label": "行为空间",
                "description": "用户平时会做的动作、习惯、小仪式。期望一个行为名列表。",
                "divergent_question": "先随便聊聊你平时会做什么。不用有条理，想到什么说什么——工作、休息、发呆、和人打交道、一个人待着的时候，你反复做的动作、习惯、小仪式，都可以讲。讲多少都行。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            }
        ]
    },
    {
        "id": "happy",
        "label": "喜悦来源",
        "fields": [
            {
                "id": "happiness_source",
                "label": "喜悦来源",
                "description": "什么情境会让用户感到喜悦。期望一个具体情境的列表，不是笼统的'开心的事'。",
                "divergent_question": "什么会让你感到喜悦？可以讲最近一次让你心里亮起来的时刻，也可以讲反复让你开心的事。不用总结，讲具体的事、具体的人、具体的场景都行。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            },
            {
                "id": "happiness_intensity",
                "label": "喜悦强度",
                "description": "喜悦来源的强度，1~5。只在 happiness_source 已填充时问。",
                "divergent_question": "刚才那些让你喜悦的事，如果按 1 到 5 打分，5 是最强烈，大概各是几？",
                "max_attempts": 3,
                "depends_on": "happiness_source",
                "value_type": "number",
            }
        ]
    },
    {
        "id": "sad",
        "label": "悲伤来源",
        "fields": [
            {
                "id": "sadness_source",
                "label": "悲伤来源",
                "description": "什么情境会让用户低落、难受。期望一个具体情境的列表。",
                "divergent_question": "反过来，什么会让你低落、难受、或者心里堵得慌？可以是一句话、一个场景、一段时间。不用分类，讲出来就行。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            }
        ]
    },
    {
        "id": "behavior_habit",
        "label": "行为习惯",
        "fields": [
            {
                "id": "behavior_habit",
                "label": "行为习惯",
                "description": "用户在某种情境下会做什么。期望'情境 → 行为'的配对列表。",
                "divergent_question": "遇到某种情况的时候，你通常会怎么做？比如累了会怎样、烦了会怎样、开心了会怎样、被人否定会怎样。想到什么说什么，不用全面。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            }
        ]
    },
    {
        "id": "memory",
        "label": "记忆片段",
        "fields": [
            {
                "id": "memory_fragment",
                "label": "记忆片段",
                "description": "用户记得特别清楚的经历。期望一个记忆列表，每条包含场景和感受。",
                "divergent_question": "有哪段经历你记得特别清楚？可以是很久以前的，也可以是最近的。当时你在做什么、和谁、心里是什么感觉，都可以讲。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            }
        ]
    },
    {
        "id": "dictionary",
        "label": "你的词库",
        "fields": [
            {
                "id": "dictionary",
                "label": "你的词库",
                "description": "用户常用、有分量的词，以及用户对这些词的解释。期望'词 → 含义'的列表。",
                "divergent_question": "最后，说说你常用的词。那些你反复用、并且对你有点分量的词——口头禅、你形容感受时会用的词、你对某个词有特殊理解的，都可以讲。",
                "max_attempts": 3,
                "depends_on": None,
                "value_type": "list",
            }
        ]
    },
]


def get_field(field_id):
    for dim in DIMENSIONS:
        for f in dim["fields"]:
            if f["id"] == field_id:
                return f
    return None


def get_dimension(dim_id):
    for dim in DIMENSIONS:
        if dim["id"] == dim_id:
            return dim
    return None


def all_fields_in_order():
    result = []
    for dim in DIMENSIONS:
        for f in dim["fields"]:
            result.append({**f, "dimension_id": dim["id"], "dimension_label": dim["label"]})
    return result