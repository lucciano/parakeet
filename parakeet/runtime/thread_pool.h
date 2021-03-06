#ifndef _THREAD_POOL_H_
#define _THREAD_POOL_H_

#include <pthread.h>
#include <stdint.h>

#include "job.h"

typedef void (*work_function_t)(int, int, void*, int64_t*);

typedef enum {
  THREAD_RUN = 0,
  THREAD_FINISHED,
  THREAD_PAUSE,
  THREAD_IDLE,
  THREAD_STOP
} thread_status_t;

typedef struct {
  task_list_t       *task_list;
  pthread_mutex_t    mutex;
  pthread_cond_t     cond;
  thread_status_t    status;
  pthread_cond_t    *master_cond;
  int                notify_when_done;
  work_function_t    work_function;
  void              *args;
  int64_t           *tile_sizes;
  int64_t            iters_done;
  int64_t            total_iters_done;
  unsigned long long time_working;
} worker_data_t;

typedef struct {
  pthread_t          *workers;
  int                 num_workers;
  int                 num_active;
  pthread_cond_t      master_cond;
  worker_data_t      *worker_data;
  int64_t            *iters_done;
  unsigned long long *timestamps;
  job_t              *job;
} thread_pool_t;

thread_pool_t *create_thread_pool(int max_threads);
void launch_job(thread_pool_t *thread_pool,
                work_function_t *work_functions, void **args, job_t *job,
                int64_t **tile_sizes, int reset_tps, int reset_iters);
void pause_job(thread_pool_t *thread_pool);
int job_finished(thread_pool_t *thread_pool);
int64_t get_iters_done(thread_pool_t *thread_pool);
double *get_throughputs(thread_pool_t *thread_pool);
void wait_for_job(thread_pool_t *thread_pool);
job_t *get_job(thread_pool_t *thread_pool);
void destroy_thread_pool(thread_pool_t *thread_pool);

#endif // _THREAD_POOL_H_
