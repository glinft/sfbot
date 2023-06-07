module.exports = {
  apps: [
    {
      name: 'sfbot',
      script: '/home/ezoweb/devel/sfbot/app.py',
      interpreter: 'python3',
      args: [],
      // args: ['--arg1', 'value1', '--arg2', 'value2'],
      watch: false,
      autorestart: true,
      max_restarts: 5,
      // log_date_format: 'YYYY-MM-DD HH:mm:ss',
      // out_file: 'path/to/log/out.log',
      // error_file: 'path/to/log/error.log',
      merge_logs: true,
    },
  ],
};
