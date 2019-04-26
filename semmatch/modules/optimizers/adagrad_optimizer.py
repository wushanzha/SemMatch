from typing import List
import tensorflow as tf
from semmatch.utils import register
from semmatch.modules.optimizers import Optimizer, MyDecoupledWeightDecayExtension


@register.register_subclass('optimizer', 'adagrad')
class AdagradOptimizer(Optimizer):
    def __init__(self, learning_rate: float = 0.05,
                 initial_accumulator_value=0.1,
                 warmup_proportion: float = None,
                 embedding_learning_rate: float = None,
                 embedding_trainable: bool = True,
                 exclude_from_weight_decay: List[str] = None,
                 weight_decay_rate: float = 0.0,
                 decay_steps: int = None, decay_rate: float = None, decay_type='polynomial',
                 optimizer_name: str = 'adagrad_optimizer'):
        super().__init__(learning_rate=learning_rate, embedding_learning_rate=embedding_learning_rate,
                         optimizer_name=optimizer_name, embedding_trainable=embedding_trainable,
                         weight_decay_rate=weight_decay_rate, exclude_from_weight_decay=exclude_from_weight_decay)
        self._learning_rate = learning_rate
        self._initial_accumulator_value = initial_accumulator_value
        self._warmup_proportion = warmup_proportion
        self._model_optimizer = None
        self._embedding_optimizer = None
        self._decay_steps = decay_steps
        self._decay_rate = decay_rate
        self._decay_type = decay_type

    def get_or_create_optimizer(self, params):
        if self._optimizer:
            return self._optimizer
        else:
            schedule = self.get_schedule(params.train_steps, self._warmup_proportion,
                                         self._decay_steps, self._decay_rate, self._decay_type)
            learning_rate = self._learning_rate * schedule

            embedding_learning_rate = self._embedding_learning_rate * schedule

            weight_decay = self._weight_decay_rate

            self._model_optimizer = AdagradWOptimizer(learning_rate=learning_rate,
                                                      weight_decay=weight_decay,
                                                              initial_accumulator_value=self._initial_accumulator_value,
                                                               name="adagrad_model_optimizer")

            self._embedding_optimizer = AdagradWOptimizer(learning_rate=embedding_learning_rate,
                                                          weight_decay=weight_decay,
                                                                  initial_accumulator_value=self._initial_accumulator_value,
                                                                  name="adagrad_embedding_optimizer")
            return self._optimizer, self._embedding_optimizer


class AdagradWOptimizer(MyDecoupledWeightDecayExtension, tf.train.AdagradOptimizer):
    def __init__(self, weight_decay, learning_rate: float = 0.05,
                 initial_accumulator_value=0.1,
                 name='adagrad_weight_decay_optimizer'):
        super(AdagradWOptimizer, self).__init__(weight_decay, learning_rate=learning_rate,
                                                initial_accumulator_value=initial_accumulator_value, name=name)