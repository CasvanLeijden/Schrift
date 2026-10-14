import * as esbuild from 'esbuild'

const watch = process.argv.includes('--watch')
const options = {
  entryPoints: { app: 'src/app.js', editor: 'src/editor/index.js' },
  outdir: '../schrift/static/dist',
  bundle: true,
  minify: !watch,
  sourcemap: watch ? 'inline' : false,
  format: 'iife',
  target: ['es2020', 'safari15'],
  legalComments: 'none',
  logLevel: 'info',
}

if (watch) {
  const ctx = await esbuild.context(options)
  await ctx.watch()
} else {
  await esbuild.build(options)
}
