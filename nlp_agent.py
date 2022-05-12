from dataclasses import dataclass
from collections import OrderedDict
import re
import pandas as pd
from transformers import pipeline


class NLPModels:
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(NLPModels, cls).__new__(cls)
            cls.zero_shot = pipeline('zero-shot-classification', model='facebook/bart-large-mnli')
            cls.question_answerer = pipeline('question-answering', model='distilbert-base-cased-distilled-squad')
        return cls.instance


@dataclass
class NLPModelsHelper:
    zero_shot_threshold: float = 0.6

    def __post_init__(self):
        self.nlp_models = NLPModels()

    def zero_shot(self, prompt, candidate_label):
        return (self.nlp_models.zero_shot(prompt, candidate_labels=[candidate_label])['scores'][0]
                > self.zero_shot_threshold)

    def zero_shot_multiple(self, prompt, candidate_labels):
        df = pd.DataFrame(self.nlp_models.zero_shot(prompt, candidate_labels=candidate_labels))
        return df.iloc[df['scores'].idxmax()]['labels']

    def question_answerer(self, question, context):
        return self.nlp_models.question_answerer(question=question, context=context)['answer']


@dataclass
class NLPField(NLPModelsHelper):
    name: str = ''
    other_names: list = None

    def __post_init__(self):
        super().__post_init__()
        self.other_names = [] if self.other_names is None else self.other_names

    def found_inside(self, prompt):
        if self.zero_shot(prompt, self.name):
            return True
        else:
            for name in self.other_names:
                if self.zero_shot(prompt, name):
                    return True
            return False

    def which_element(self, prompt):
        candidate = self.zero_shot_multiple(prompt, self.elements)
        return candidate if self.zero_shot(prompt, candidate) else None

    def element_name(self, prompt, element):
        question = f'Which is the name of the {element}?'
        name = self.question_answerer(question, prompt)
        return None if name == '' else name.split()[0]

    def action(self, prompt, element, element_name):
        question = f'What should we do with the {element} {element_name}?'
        return self.question_answerer(question, prompt)

    def overall_direction(self, prompt):
        question = f'To where?'
        return self.question_answerer(question, prompt)

    def direction(self, prompt):
        return self.zero_shot_multiple(prompt, self.directions)

    def amount(self, prompt):
        question = f'By how much?'
        return self.question_answerer(question, prompt)

    @staticmethod
    def amount_breakdown(amount):
        try:
            number = re.findall(r'(?<![a-zA-Z:])[-+]?\d*\.?\d+', amount)[0]
        except IndexError:
            return None
        unit = ''.join([letter for letter in amount if letter.isalnum() and not letter.isdigit()])
        unit = 'm' if unit == '' else unit  # defaulting to meters
        return float(number), unit

    def on_fail(self, item, parsed_items=None):
        if parsed_items is None:
            txt = 'Sorry, '
        else:
            txt = f'Hey! I understood {self.summary(parsed_items)}. However, '
        txt += f'I could not understand which {item} you are talking about. '
        txt += f'Would you mind to rephrase it in a clear way for me and try again? Thanks in advance!'
        return txt

    def discard_items(self, parsed_items):
        return {key: value for key, value in parsed_items.items() if key not in self.parsed_items_to_discard}

    @staticmethod
    def parse_amount(parsed_items):
        parsed_items['number'], parsed_items['unit'] = parsed_items['number_unit']
        return parsed_items


@dataclass
class NLPStructuralField(NLPField):
    def __post_init__(self):
        super().__post_init__()
        self.elements = ['wall', 'slab', 'beam', 'column']
        self.directions = ['up', 'down', 'front', 'back', 'left', 'right']

    @property
    def nlp_recipe(self):
        return OrderedDict({
            'element': {
                'func': self.which_element,
                'args': 'prompt'
            },
            'element_name': {
                'func': self.element_name,
                'args': ['prompt', 'element']
            },
            'action': {
                'func': self.action,
                'args': ['prompt', 'element', 'element_name']
            },
            'overall_direction': {
                'func': self.overall_direction,
                'args': ['prompt'],
                'readable_value_name': 'direction'
            },
            'direction': {
                'func': self.direction,
                'args': 'overall_direction'
            },
            'amount': {
                'func': self.amount,
                'args': 'action'
            },
            'number_unit': {
                'func': self.amount_breakdown,
                'args': 'amount',
                'readable_value_name': 'amount'
            }
        })

    @property
    def parsed_items_to_discard(self):
        return ['prompt', 'action', 'overall_direction', 'amount', 'number_unit']

    @staticmethod
    def summary(parsed_items):
        txt = ''
        if 'element' not in parsed_items:
            return txt
        txt += f'a {parsed_items["element"]}'
        if 'element_name' not in parsed_items:
            return txt
        txt += f' named {parsed_items["element_name"]}'
        if 'direction' not in parsed_items:
            return txt
        txt += f' is requested to be moved {parsed_items["direction"]}'
        if 'number' not in parsed_items:
            return txt
        txt += f' by {parsed_items["number"]} {parsed_items["unit"]}'
        return txt

    def on_success(self, parsed_items):
        return f'Hey! Perfect, I understood that {self.summary(parsed_items)}. Doing my job now!'

    def process_one_step(self, value_name, parsed_items, func, args=None, readable_value_name=None):
        if args is None:
            args = []
        else:
            args = args if isinstance(args, list) else [args]
            args = [parsed_items[arg] for arg in args]
        value = func(*args)
        if value is None:
            value_name = value_name if readable_value_name is None else readable_value_name
            return {**parsed_items, **{'answer': self.on_fail(value_name, parsed_items)}}
        parsed_items[value_name] = value
        return parsed_items

    def process_prompt(self, prompt):
        parsed_items = {'success': False, 'prompt': prompt}
        for step_name, step_data in self.nlp_recipe.items():
            func = step_data['func']
            args = step_data['args'] if 'args' in step_data else []
            readable_value_name = step_data['readable_value_name'] if 'readable_value_name' in step_data else None
            parsed_items = self.process_one_step(step_name, parsed_items, func, args, readable_value_name)
            if step_name not in parsed_items:
                return self.discard_items(parsed_items)

        parsed_items['success'] = True
        parsed_items = self.parse_amount(parsed_items)
        parsed_items = self.discard_items(parsed_items)
        return {**parsed_items, **{'answer': self.on_success(parsed_items)}}


@dataclass
class NLPAgent(NLPModelsHelper):
    def __post_init__(self):
        self.fields = self.setup_fields()
        self.nlp_models = NLPModels()

    def setup_fields(self):
        structural = NLPStructuralField(name='structural')
        mep = NLPField(name='MEP', other_names=['mechanical', 'electrical', 'plumbing', 'fire protection'])
        return [structural, mep]

    def process_prompt(self, prompt):
        if not self.zero_shot(prompt, candidate_label='request'):
            answer = 'Please, a request must be placed.'
            return {'success': False, 'answer': answer}
        for field in self.fields:
            if field.found_inside(prompt):
                return field.process_prompt(prompt)
        return None


if __name__ == '__main__':
    queries = [
        'Please, lower by 0.2 m the height of beam B125',
        'Can you move 0.5m to the front a wall W15?',
        'Displace column C27 one meter to the left, please',
        'Displace column C27 1 m to the left, please'
    ]

    nlp_agent = NLPAgent()
    for query in queries:
        request_data = nlp_agent.process_prompt(query)
        print(request_data)
