import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from datautils import MyTrainDataset

import torch.multiprocessing as mp
from torch.utils.data.distributed import DistributedSampler
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.distributed import init_process_group, destroy_process_group
import os

def ddp_setup(rank, world_size):

    """

    :param rank: Unique Identifier of each process.
    :param world_size: Total number of processes.
    :return:
    """
    os.environ['MASTER_ADDR'] = "localhost"
    os.environ['MASTER_PORT'] = "12355"

    torch.cuda.set_device(rank)
    init_process_group(backend='nccl', rank = rank, world_size=world_size)


class Trainer:
    def __init__(
            self,
            model: torch.nn.Module,
            optimizer: torch.optim.Optimizer,
            train_data: DataLoader,
            gpu_id: int,
            save_every: int,
                 ) -> None:
        self.gpu_id = gpu_id
        self.model = model.to(self.gpu_id)
        self.train_data = train_data
        self.optimizer = optimizer
        self.save_every = save_every
        self.model = DDP(self.model, device_ids=[gpu_id])

    def _run_batch(self, source, targets):
        self.optimizer.zero_grad()
        outputs = self.model(source)
        loss = F.cross_entropy(outputs, targets)
        loss.backward()
        self.optimizer.step()

    def _run_epoch(self, epoch):
        batch_size = len(next(iter(self.train_data))[0])

        print(f"[GPU{self.gpu_id}] | Batch size: {batch_size}] | Steps = {len(self.train_data)}")
        self.train_data.sampler.set_epoch(epoch)

        for source, targets in self.train_data:
            source = source.to(self.gpu_id)
            targets = targets.to(self.gpu_id)
            self._run_batch(source, targets)

    def _save_checkpoint(self, epoch):
        checkpoint = self.model.module.state_dict()
        PATH = "checkpoint.pt"
        torch.save(checkpoint, PATH)
        print(f"Epoch {epoch} | Training Checkpoint saved at {PATH}")


    def train(self, max_epochs : int):
        for epoch in range(max_epochs):
            self._run_epoch(epoch)
            if self.gpu_id == 0 and epoch % self.save_every == 0:
                self._save_checkpoint(epoch)


def load_training_objects():
    train_set = MyTrainDataset(2048)
    model = torch.nn.Linear(20, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr = 1e-3)
    return train_set, model, optimizer


def prepare_dataloader(dataset: Dataset, batch_size: int):
    return DataLoader(dataset, batch_size = batch_size, pin_memory = True, shuffle = False, sampler = DistributedSampler(dataset))


def main(rank: int, world_size: int, save_every: int, total_epochs: int, batch_size: int):
    ddp_setup(rank, world_size)
    dataset, model, optimizer = load_training_objects()

    trainer = Trainer(model, optimizer, dataset, rank, save_every)

    trainer.train(total_epochs)

    destroy_process_group()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Simple Distributed Training Job.')
    parser.add_argument('total_epochs', type=int, help='Total epochs to train the model')
    parser.add_argument('save_every', type=int, help='How often to save a snapshot')
    parser.add_argument('--batch_size', default=32, type=int, help='Input batch size on each device (default: 32)')
    args = parser.parse_args()


    world_size = torch.cuda.device_count()

    mp.spawn(main, args = (world_size, args.save_every, args.total_epochs, args.batch_size), nprocs=world_size)


