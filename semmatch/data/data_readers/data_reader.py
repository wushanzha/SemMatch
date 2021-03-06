import os
import six
import random
import collections
from typing import Dict, List
import tensorflow as tf
from semmatch.data import vocabulary
from semmatch.utils.logger import logger
from semmatch.utils.path import paths_all_exist
from semmatch.data.data_utils import cpu_count
from semmatch.utils import register
from semmatch.config.init_from_params import InitFromParams
from semmatch.utils.exception import ConfigureError
import tqdm


VOCABULARY_DIR = "vocabulary"


def cast_ints_to_int32(features):
  f = {}
  for k, v in sorted(six.iteritems(features)):
    if v.dtype in [tf.int64, tf.uint8]:
      v = tf.cast(v, tf.int32)
    f[k] = v
  return f


class DataSplit(object):
  TRAIN = tf.estimator.ModeKeys.TRAIN
  EVAL = tf.estimator.ModeKeys.EVAL
  TEST = "test"
  PREDICT = tf.estimator.ModeKeys.PREDICT


@register.register('data')
class DataReader(InitFromParams):
    def __init__(self, data_path: str = None, tmp_path: str = None, data_name: str = None, batch_size: int = 32,
                 train_filename: str = None, valid_filename: str = None, test_filename: str = None, num_label: int = None,
                 predict_filename: str = None, max_length: int = None,
                 vocab_init_files: Dict[str, str] = None, concat_sequence: bool = False,
                 num_retrieval: int = None, generate_tfrecord: bool = True, resample: bool = False,
                 emb_pretrained_files: Dict[str, str] = None, only_include_pretrained_words: bool = False) -> None:
        self._data_name = data_name or "data"
        if data_path is None:
            raise LookupError("The data path of dataset %s is not found." % data_path)
        self._data_path = data_path
        if tmp_path is None:
            logger.warning("The tmp path of dataset is not found. The tmp path is set as data path %s" % data_path)
            self._tmp_path = data_path
        else:
            self._tmp_path = tmp_path
        if predict_filename is None:
            predict_filename = test_filename
        self._mode_2_filename = {DataSplit.TRAIN: train_filename, DataSplit.EVAL: valid_filename,
                                 DataSplit.TEST: test_filename, DataSplit.PREDICT: predict_filename}
        self._vocab = None
        self._batch_size = batch_size
        self._max_length = max_length
        self._emb_pretrained_files = emb_pretrained_files
        self._only_include_pretrained_words = only_include_pretrained_words
        self._vocab_init_files = vocab_init_files
        self._concat_sequence = concat_sequence
        self._num_retrieval = num_retrieval
        self._generate_tfrecord = generate_tfrecord
        self._num_label = num_label
        self._resample = resample

    def get_data_name(self):
        return self._data_name

    def get_num_retrieval(self):
        return self._num_retrieval

    def get_filename_by_mode(self, mode):
        filename = self._mode_2_filename.get(mode, None)
        return filename

    def _read(self, mode):
        raise NotImplementedError

    def read(self, mode):
        try:
            instances = self._read(mode)
        except Exception as e:
            logger.error(e)
            return None
        return instances

    def get_padded_shapes_and_values(self, mode):
        instances = self.read(mode)
        try:
            instance = next(instances)
            padded_shapes = instance.get_padded_shapes()
            padding_values = instance.get_padding_values()
        except StopIteration as e:
            padded_shapes = None
            padding_values = None
            logger.warning("The %s part of data gain tfrecord file features error. "
                           "If the filename of this part is not provided, please ignore this warning" % mode)
        return padded_shapes, padding_values

    def get_raw_serving_input_receiver_features(self, mode):
        instances = self.read(mode)
        try:
            instance = next(instances)
            feautres = instance.get_raw_serving_input_receiver_features()
        except StopIteration as e:
            feautres = None
            logger.warning("The %s part of data gain tfrecord file features error. "
                           "If the filename of this part is not provided, please ignore this warning" % mode)
        return feautres

    def get_features(self, mode):
        instances = self.read(mode)
        try:
            instance = next(instances)
            feautres = instance.get_example_features()
        except StopIteration as e:
            feautres = None
            logger.warning("The %s part of data gain tfrecord file features error. "
                           "If the filename of this part is not provided, please ignore this warning" % mode)
        return feautres

    def get_vocab(self):
        if self._vocab is None:
            self._vocab = self.get_or_create_vocab()
        return self._vocab

    def make_estimator_input_fn(self, mode, force_repeat=False):
        if self._vocab is None:
            self._vocab = self.get_or_create_vocab()
        if self._generate_tfrecord:
            self.generate(self._vocab, mode)
        features = self.get_features(mode)
        padded_shapes, padding_values = self.get_padded_shapes_and_values(mode)
        print(padded_shapes, padding_values)
        if features and padded_shapes:
            def input_fn(params=None, config=None):
                if params is None:
                    params = {}
                if config is None:
                    config = {}
                return self.input_fn(features, padded_shapes, padding_values, mode, params=params, config=config, force_repeat=force_repeat)
            return input_fn
        else:
            return None

    def resample_data_reject(self, dataset):
        def class_func(parsed_features):
            # tf.where(tf.not_equal(tf.cast(parsed_features['label/labels'], tf.float32), tf.constant(0, dtype=tf.float32)))[:, 0]
            # tf.where(tf.not_equal(parsed_features['label/labels'], zero))
            if parsed_features['label/labels'].get_shape()[0] == 1:
                return parsed_features['label/labels'][0]
            else:
                return tf.argmax(parsed_features['label/labels'])

        resampler = tf.data.experimental.rejection_resample(
            class_func, target_dist=[1.0 / self._num_label for i in range(self._num_label)])
        dataset = dataset.apply(resampler).map(lambda extra_label, features_and_label: features_and_label)
        return dataset

    def resample_data_sample(self, dataset):
        def filter_class_func(parsed_features):
            # tf.where(tf.not_equal(tf.cast(parsed_features['label/labels'], tf.float32), tf.constant(0, dtype=tf.float32)))[:, 0]
            # tf.where(tf.not_equal(parsed_features['label/labels'], zero))
            if parsed_features['label/labels'].get_shape()[0] == 1:
                return parsed_features['label/labels'][0] == filter_label
            else:
                return tf.argmax(parsed_features['label/labels']) == filter_label

        dataset_classes = []
        for filter_label in range(self._num_label):
            dataset_class = dataset.filter(filter_class_func)
            dataset_classes.append(dataset_class)
        dataset = tf.data.experimental.sample_from_datasets(dataset_classes, [1.0 / self._num_label for i in range(self._num_label)])

        return dataset

    def input_fn(self, features, padded_shapes, padding_values, mode, params, config, force_repeat=False):
        is_training = mode == tf.estimator.ModeKeys.TRAIN
        num_threads = cpu_count() - 1 if is_training else 1
        batch_size = self._batch_size
        dataset = self.dataset(features, mode, params, num_threads=num_threads)

        if force_repeat or is_training:
            dataset = dataset.repeat()
            print("dataset repeat")

        dataset = dataset.map(
            cast_ints_to_int32, num_parallel_calls=num_threads)
        # if self._resample and is_training:
        #     dataset = self.resample_data_sample(dataset)
        #dataset = dataset.batch(batch_size)
        dataset = dataset.padded_batch(batch_size, padded_shapes=padded_shapes, padding_values=padding_values)
        dataset = dataset.prefetch(2)
        return dataset

    def dataset(self, features, mode, params, num_threads=None, output_buffer_size=None, shuffle_files=None, shuffle_buffer_size=1024):
        def _parse_function(example_proto, features):
            context_parsed, sequence_parsed = tf.parse_single_sequence_example(
                example_proto, context_features=features[1],
                sequence_features=features[0])
            parsed_features = {**context_parsed, **sequence_parsed}
            if self._concat_sequence:
                new_parsed_features = dict()
                for feature_key, feature_tensor in parsed_features.items():
                    feature_name, field_name = feature_key.split("/")
                    if feature_name == 'premise':
                        hypothesis_key = "hypothesis"+'/'+field_name
                        hypothesis_tensor = parsed_features[hypothesis_key]
                        concat_tensor = tf.concat([feature_tensor, hypothesis_tensor[1:]], axis=0)
                        new_parsed_features[feature_key] = concat_tensor
                    else:
                        new_parsed_features[feature_key] = feature_tensor
                parsed_features = new_parsed_features
            return parsed_features

        is_training = mode == tf.estimator.ModeKeys.TRAIN
        shuffle_files = shuffle_files or shuffle_files is None and is_training
        if params.get('task', 'classification') == 'rank':
            shuffle_files = False
        if self._generate_tfrecord:
            filenames = self._get_output_file_paths(mode)
            if is_training:
                dataset = tf.data.Dataset.from_tensor_slices(tf.constant(filenames))
                if shuffle_files:
                    dataset.shuffle(buffer_size=len(filenames))
                #dataset.repeat()
                cycle_length = min(num_threads, len(filenames))
                # dataset = dataset.apply(
                #     tf.data.experimental.parallel_interleave(
                #         tf.data.TFRecordDataset,
                #         sloppy=is_training,
                #         cycle_length=cycle_length))

                dataset = dataset.interleave(tf.data.TFRecordDataset, cycle_length=cycle_length)
                dataset = dataset.shuffle(buffer_size=shuffle_buffer_size)
            else:
                dataset = tf.data.TFRecordDataset(filenames)
                if shuffle_files:
                    dataset = dataset.shuffle(shuffle_buffer_size)
            dataset = dataset.map(lambda record: _parse_function(record, features), num_parallel_calls=num_threads)
        else:
            dataset_dict = collections.defaultdict(list)
            for instance in tqdm.tqdm(self.read(mode)):
                instance.index_fields(self._vocab)
                raw_data = instance.to_raw_data()
                for key in raw_data:
                    dataset_dict[key].append(raw_data[key])
                print(raw_data)

        if output_buffer_size:
            dataset = dataset.prefetch(output_buffer_size)
        return dataset

    def _get_num_shards(self, mode):
        num_shards = 1
        if mode == DataSplit.TRAIN:
            num_shards = 10
        return num_shards

    def _get_output_file_paths(self, mode):
        paths = []
        num_shards = self._get_num_shards(mode)
        filename = self.get_filename_by_mode(mode)
        if filename:
            if isinstance(filename, List):
                filename = filename[0]
            basefilename = os.path.splitext(os.path.basename(filename))[0]
            for i in range(num_shards):
                filename = "%s_%s_%s_of_%s.tfrecord"%(self._data_name, basefilename, i, num_shards)
                paths.append(os.path.join(self._tmp_path, filename))
        return paths

    def generate(self, vocab, mode, cycle_every_n=1):
        logger.info("generating tfrecord files")
        output_filenames = self._get_output_file_paths(mode)
        if paths_all_exist(output_filenames):
            logger.info("Skipping tfrecord files generating because tfrecord files exists at {}"
                            .format(self._tmp_path))
            return
        tmp_filenames = [fname + ".incomplete" for fname in output_filenames]
        num_shards = len(output_filenames)
        # # Check if is training or eval, ref: train_data_filenames().
        # if num_shards > 0:
        #     if "-train" in output_filenames[0]:
        #         tag = "train"
        #     elif "-dev" in output_filenames[0]:
        #         tag = "eval"
        #     else:
        #         tag = "other"

        writers = [tf.python_io.TFRecordWriter(fname) for fname in tmp_filenames]
        counter, shard = 0, 0
        try:
            for instance in tqdm.tqdm(self.read(mode)):
                instance.index_fields(vocab)
                counter += 1
                example = instance.to_example()
                writers[shard].write(example.SerializeToString())
                if counter % cycle_every_n == 0:
                    shard = (shard + 1) % num_shards
        except StopIteration as e:
            logger.error("Generate data error for %s part in dataset. "
                         "If the filename of this part is not provided, please ignore this warning"%mode)

        for writer in writers:
            writer.close()

        for tmp_name, final_name in zip(tmp_filenames, output_filenames):
            tf.gfile.Rename(tmp_name, final_name)
        logger.info("Generated %s Examples", counter)
        return output_filenames

    def get_or_create_vocab(self):
        vocab_dir = os.path.join(self._tmp_path, VOCABULARY_DIR)
        logger.info("get or create vocabulary from %s.", vocab_dir)
        vocab = vocabulary.Vocabulary(pretrained_files=self._emb_pretrained_files,
                                      vocab_init_files=self._vocab_init_files,
                                      only_include_pretrained_words=self._only_include_pretrained_words)
        if not vocab.load_from_files(vocab_dir):
            instances = self.read(DataSplit.TRAIN)
            vocab = vocabulary.Vocabulary.init_from_instances(instances,
                                                              pretrained_files=self._emb_pretrained_files,
                                                              vocab_init_files=self._vocab_init_files,
                                                              only_include_pretrained_words=self._only_include_pretrained_words)
            vocab.save_to_files(vocab_dir)
        return vocab


    # def generate_data(self, data_dir, tmp_dir, task_id=-1):
    #
    #     filepath_fns = {
    #         problem.DatasetSplit.TRAIN: self.training_filepaths,
    #         problem.DatasetSplit.EVAL: self.dev_filepaths,
    #         problem.DatasetSplit.TEST: self.test_filepaths,
    #     }
    #
    #     split_paths = [(split["split"], filepath_fns[split["split"]](
    #         data_dir, split["shards"], shuffled=self.already_shuffled))
    #                    for split in self.dataset_splits]
    #     all_paths = []
    #     for _, paths in split_paths:
    #       all_paths.extend(paths)
    #
    #     if self.is_generate_per_split:
    #       for split, paths in split_paths:
    #         generator_utils.generate_files(
    #             self._maybe_pack_examples(
    #                 self.generate_encoded_samples(data_dir, tmp_dir, split)), paths)
    #     else:
    #       generator_utils.generate_files(
    #           self._maybe_pack_examples(
    #               self.generate_encoded_samples(
    #                   data_dir, tmp_dir, problem.DatasetSplit.TRAIN)), all_paths)
    #
    #     generator_utils.shuffle_dataset(all_paths)
    #
    # def _load_records_and_preprocess(filenames):
    #   """Reads files from a string tensor or a dataset of filenames."""
    #   # Load records from file(s) with an 8MiB read buffer.
    #   dataset = tf.data.TFRecordDataset(filenames, buffer_size=8 * 1024 * 1024)
    #   # Decode.
    #   dataset = dataset.map(self.decode_example, num_parallel_calls=num_threads)
    #   # Preprocess if requested.
    #   # Note that preprocessing should happen per-file as order may matter.
    #   if preprocess:
    #     dataset = self.preprocess(dataset, mode, hparams,
    #                               interleave=shuffle_files)
    #   return dataset
    #
    # if len(data_files) < num_partitions:
    #   raise ValueError(
    #       "number of data files (%d) must be at least the number of hosts (%d)"
    #       % (len(data_files), num_partitions))
    # data_files = [f for (i, f) in enumerate(data_files)
    #               if i % num_partitions == partition_id]
    # tf.logging.info(
    #     "partition: %d num_data_files: %d" % (partition_id, len(data_files)))
    # if shuffle_files:
    #   mlperf_log.transformer_print(key=mlperf_log.INPUT_ORDER)
    #   random.shuffle(data_files)
    #
    # dataset = tf.data.Dataset.from_tensor_slices(tf.constant(data_files))
    # # Create data-set from files by parsing, pre-processing and interleaving.
    # if shuffle_files:
    #   dataset = dataset.apply(
    #       tf.contrib.data.parallel_interleave(
    #           _load_records_and_preprocess, sloppy=True, cycle_length=8))
    # else:
    #   dataset = _load_records_and_preprocess(dataset)
    #
    # dataset = dataset.map(
    #     self.maybe_reverse_and_copy, num_parallel_calls=num_threads)
    # dataset = dataset.take(max_records)
    #
    # ## Shuffle records only for training examples.
    # if shuffle_files and is_training:
    #   dataset = dataset.shuffle(shuffle_buffer_size)
    # if output_buffer_size:
    #   dataset = dataset.prefetch(output_buffer_size)
    #
    # return dataset

