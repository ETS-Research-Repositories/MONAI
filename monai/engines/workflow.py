# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from abc import ABC, abstractmethod
import torch
from ignite.engine import Engine, State, Events
from .utils import default_prepare_batch


class Workflow(ABC):
    """Workflow defines the core work process totally based on Ignite engine.
    All trainer, validator and evaluator share this same workflow as base class,
    because they all can be treated as same Ignite engine loops.
    And it initializes all the sharable data in Ignite engine.state.
    Attach additional processing logics to Ignite engine based on Event-Handler mechanism.

    Args:
        device (torch.device): an object representing the device on which to run.
        max_epochs (int): the total epoch number for engine to run, validator and evaluator have only 1 epoch.
        amp (bool): whether to enable auto-mixed-precision training.
        data_loader (torch.DataLoader): Ignite engine use data_loader to run, must be torch.DataLoader.
        prepare_batch (Callable): function to parse image and label for current iteration.
        key_metric (ignite.metric): compute metric when every iteration completed, and save average
            value to engine.state.metrics when epoch completed. also use key_metric to select and
            save checkpoint into files.
        additional_metrics (list): more ignite metrics that also attach to Ignite Engine.
        handlers (list): every handler is a set of Ignite Event-Handlers, like:
            CheckpointHandler, StatsHandler, TimerHandler, etc.

    """

    def __init__(
        self,
        device,
        max_epochs,
        amp,
        data_loader,
        prepare_batch=default_prepare_batch,
        key_metric=None,
        additional_metrics=None,
        handlers=None,
    ):
        # FIXME:
        if amp:
            print("Will add AMP support when PyTorch v1.6 released.")
        assert isinstance(device, torch.device), "must provide PyTorch device information."
        assert isinstance(max_epochs, int), "must set max epoch number."
        assert isinstance(data_loader, torch.utils.data.DataLoader), "data_loader must be PyTorch DataLoader."

        self.engine = Engine(self._iteration)
        # set all sharable data for the workflow based on Ignite engine.state
        self.engine.state = State(
            seed=0,
            iteration=0,
            epoch=0,
            max_epochs=max_epochs,
            epoch_length=-1,
            output=None,
            batch=None,
            metrics={},
            dataloader=None,
            device=device,
            amp=amp,
            key_metric_name=None,  # we can set many metrics, only use key_metric to compare and save the best model
            best_metric=-1,
            best_metric_epoch=-1,
        )
        self.data_loader = data_loader
        self.prepare_batch = prepare_batch

        metrics = None
        if key_metric is not None:
            assert isinstance(key_metric, dict), "key_metric must be a dict object."
            self.engine.state.key_metric_name = list(key_metric.keys())[0]
            metrics = key_metric
            if additional_metrics is not None and len(additional_metrics) > 0:
                assert isinstance(additional_metrics, dict), "additional_metrics must be a dict object."
                metrics.update(additional_metrics)
            for name, metric in metrics.items():
                metric.attach(self.engine, name)

            @self.engine.on(Events.EPOCH_COMPLETED)
            def post_epoch_process(engine):
                if engine.state.key_metric_name is not None:
                    current_val_metric = engine.state.metrics[engine.state.key_metric_name]
                    if current_val_metric > engine.state.best_metric:
                        print("Got new best metric of {}: {}".format(engine.state.key_metric_name, current_val_metric))
                        engine.state.best_metric = current_val_metric
                        engine.state.best_metric_epoch = engine.state.epoch

        if handlers is not None and len(handlers) > 0:
            assert isinstance(handlers, (list, tuple)), "handlers must be a chain."
            for handler in handlers:
                handler.attach(self.engine)

    def _run(self):
        """Execute training, validation or evaluation based on Ignite Engine.

        """
        self.engine.state.iteration = 0
        self.engine.run(data=self.data_loader, epoch_length=len(self.data_loader))

    @abstractmethod
    def _iteration(self, engine, batchdata):
        """Abstract callback function for the processing logic of 1 iteration in Ignite Engine.
        Need subclass to implement different logics, like SupervisedTraner/Evaluator, GANTrainer, etc.

        Args:
            engine (ignite.engine): Ignite Engine, it can be a trainer, validator or evaluator.
            batchdata (TransformContext, ndarray): input data for this iteration.

        """
        raise NotImplementedError("Subclass {} must implement the compute method".format(self.__class__.__name__))
